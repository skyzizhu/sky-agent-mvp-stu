"""
Stage 11：Agent 研究工作台后端。
把 common/agent_core.py 的 ResearchAgent 注入 Web 实现（emit→SSE 队列，
approver→网页按钮，stop_check→中止按钮），提供研究运行的事件流。

运行: .venv/bin/python -m uvicorn webapp.app:app --port 7870
打开: http://127.0.0.1:7870
"""
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from common.agent_core import ResearchAgent
from common.guardrails import DANGEROUS_TOOLS
from common.observability import load_runs
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Agent Research Workbench")

# 运行注册表：run_id -> {"agent", "thread", "question"}
RUNS = {}


class ResearchAgentWeb(ResearchAgent):
    """Web 注入实现：emit 推进队列（SSE 读走），approver 等网页按钮，stop 查按钮。"""

    def __init__(self, run_id: str, **kw):
        super().__init__(emit=self._push, approver=self._web_approver,
                         stop_check=lambda: self._stop.is_set(), **kw)
        self.run_id = run_id
        self._stop = threading.Event()
        self._queue = []                      # 事件队列（SSE 消费）
        self._cond = threading.Condition()
        self._approval = None                 # {"event","approved","tool","args"}
        self._thread = None                   # 由路由在启动线程后回填

    # -- emit 注入：推进队列 --
    def _push(self, type: str, **p):
        with self._cond:
            self._queue.append({"type": type, **p})
            self._cond.notify_all()

    def events_since(self, idx: int) -> list:
        with self._cond:
            return self._queue[idx:]

    def event_count(self) -> int:
        with self._cond:
            return len(self._queue)

    @property
    def finished(self) -> bool:
        return self._thread is None or not self._thread.is_alive()

    # -- approver 注入：发审批事件，阻塞等网页按钮 --
    def _web_approver(self, tool: str, args_json: str):
        ev = threading.Event()
        with self._cond:
            self._approval = {"event": ev, "approved": None,
                              "tool": tool, "args": args_json}
        # 注意：approval_request 事件已由 agent_core 循环 emit，这里不再重复发
        ev.wait(600)  # 最多等10分钟，超时视为拒绝
        approved = bool(self._approval.get("approved"))
        receipt = ("用户已批准执行。" if approved else
                   "用户【拒绝】了此操作。请尊重该决定：不要重复尝试同一动作，"
                   "改为直接在回答中输出报告内容，并告知用户可自行转发。")
        self.emit("approval_result", tool=tool, approved=approved)
        return approved, receipt

    def approve(self, approved: bool) -> bool:
        a = self._approval
        if a and not a["event"].is_set():
            a["approved"] = approved
            a["event"].set()
            return True
        return False

    def stop(self):
        self._stop.set()


# ---------- 路由 ----------
@app.get("/")
def index():
    return FileResponse(ROOT / "webapp" / "static" / "index.html")


@app.get("/api/config")
def get_config():
    budget = 3000 if os.getenv("LOW_BUDGET") else 60000
    return {"model": config.MODEL, "budget": budget,
            "mcp_available": os.getenv("MCP_FS") == "1"}


class ResearchIn(BaseModel):
    question: str


@app.post("/api/research")
def start_research(body: ResearchIn):
    run_id = uuid.uuid4().hex[:8]
    agent = ResearchAgentWeb(run_id, impl="webapp",
                             use_mcp=os.getenv("MCP_FS") == "1")
    t = threading.Thread(target=agent.run, args=(body.question,), daemon=True)
    RUNS[run_id] = {"agent": agent, "thread": t, "question": body.question}
    agent._thread = t  # SSE 生成器通过它判断运行是否结束
    t.start()
    return {"run_id": run_id}


@app.get("/api/research/{run_id}/events")
def events(run_id: str):
    if run_id not in RUNS:
        return JSONResponse({"error": "run not found"}, status_code=404)

    def gen():
        agent = RUNS[run_id]["agent"]
        last = 0
        while True:
            events = agent.events_since(last)
            for e in events:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            last += len(events)
            if agent.finished and agent.event_count() <= last:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
            time.sleep(0.3)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(gen(), media_type="text/event-stream")


class ApproveIn(BaseModel):
    approved: bool


@app.post("/api/research/{run_id}/approve")
def approve(run_id: str, body: ApproveIn):
    if run_id not in RUNS:
        return JSONResponse({"error": "run not found"}, status_code=404)
    ok = RUNS[run_id]["agent"].approve(body.approved)
    return {"ok": ok}


@app.post("/api/research/{run_id}/stop")
def stop(run_id: str):
    if run_id not in RUNS:
        return JSONResponse({"error": "run not found"}, status_code=404)
    RUNS[run_id]["agent"].stop()
    return {"ok": True}


@app.get("/api/runs")
def runs_list():
    return {"runs": list(reversed(load_runs()))}

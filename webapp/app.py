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

EV_DIR = ROOT / "logs" / "events"
SESSION_DIR = ROOT / "sessions"          # 每次运行的事件存档（可回放）
from common.guardrails import DANGEROUS_TOOLS
from common.session import SessionStore
from common.observability import load_runs
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from pydantic import BaseModel

app = FastAPI(title="Agent Research Workbench")

# 运行注册表：run_id -> {"agent", "thread", "question"}
RUNS = {}
SESSION_STORE = SessionStore(SESSION_DIR)
EV_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)


class ResearchAgentWeb(ResearchAgent):
    """Web 注入实现：emit 推进队列（SSE 读走）+ 事件落盘（历史回放），
    approver 等网页按钮，stop 查按钮。"""

    def __init__(self, run_id: str, events_path=None, **kw):
        super().__init__(emit=self._push, approver=self._web_approver,
                         stop_check=lambda: self._stop.is_set(), **kw)
        self.run_id = run_id
        self._events_path = Path(events_path) if events_path else None
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
        # 事件落盘：历史详情页的数据源（与队列内容一致）
        if self._events_path:
            try:
                self._events_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._events_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": type, **p},
                                       ensure_ascii=False, default=str) + "\n")
            except Exception:
                pass

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
        # ★approval_result 由 agent_core 循环在 approver 返回后统一 emit
        #   （带 sub + receipt）；这里不再重复发——旧代码多发一条缺 sub 的
        #   事件，会让前端 addSub(undefined) 抛 TypeError，且结论显示两次
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
    budget = (3000 if os.getenv("LOW_BUDGET")
              else config.BUDGET_TIERS["standard"])
    return {"model": config.MODEL, "budget": budget,
            "tiers": config.BUDGET_TIERS,
            "mcp_available": os.getenv("MCP_FS") == "1"}


class ResearchIn(BaseModel):
    question: str
    tier: str = "standard"
    session_id: str = ""      # 空 = 新会话


@app.post("/api/research")
def start_research(body: ResearchIn):
    run_id = uuid.uuid4().hex[:8]
    budget_max = (3000 if os.getenv("LOW_BUDGET")
                  else config.BUDGET_TIERS.get(body.tier,
                       config.BUDGET_TIERS["standard"]))   # unlimited 档在配置里为 None
    session = SESSION_STORE.get_or_create(body.session_id) if body.session_id else SESSION_STORE.create()
    session.add_run(run_id, body.question[:80])
    agent = ResearchAgentWeb(run_id, impl="webapp",
                             use_mcp=os.getenv("MCP_FS") == "1",
                             budget_max=budget_max,
                             session=session,
                             events_path=EV_DIR / f"events_{run_id}.jsonl")
    # ★会话元信息写入事件流头部（队列+存档第一行）：历史回放时据此找到所属会话，
    #   实现"一次会话多轮查询"的完整时间线还原
    agent._push("run_meta", run_id=run_id, session_id=session.id,
                question=body.question)
    t = threading.Thread(target=agent.run, args=(body.question,), daemon=True)
    RUNS[run_id] = {"agent": agent, "thread": t, "question": body.question}
    agent._thread = t  # SSE 生成器通过它判断运行是否结束
    t.start()
    return {"run_id": run_id, "session_id": session.id}


@app.get("/api/research/{run_id}/events")
def events(run_id: str, since: int = 0):
    if run_id not in RUNS:
        return JSONResponse({"error": "run not found"}, status_code=404)

    def gen():
        agent = RUNS[run_id]["agent"]
        last = max(0, since)          # ★断点续传：前端重连时带上已收到的条数，不重放不漏发
        last_out = time.time()
        while True:
            events = agent.events_since(last)
            for e in events:
                yield f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
                last_out = time.time()
            last += len(events)
            if agent.finished and agent.event_count() <= last:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
            # ★心跳：工具执行/模型调用期间可能几十秒无事件——
            #   空闲连接会被 WebView/代理掐掉（实测 fetch_js 渲染时前端断流）。
            #   SSE 注释帧（冒号开头）浏览器会忽略，但能让连接保持"活跃"。
            if time.time() - last_out > 15:
                yield ": ping\n\n"
                last_out = time.time()
            time.sleep(0.3)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


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


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str):
    """历史运行的事件回放数据（含 final 事件里的报告全文 + RunLog 元信息）。"""
    p = EV_DIR / f"events_{run_id}.jsonl"
    if not p.exists():
        return {"events": [], "meta": {}}
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # 从 RunLog 取元信息
    meta = {}
    runs_file = ROOT / "logs" / "runs.jsonl"
    if runs_file.exists():
        for rl in runs_file.read_text(encoding="utf-8").splitlines():
            rl = rl.strip()
            if not rl:
                continue
            try:
                rec = json.loads(rl)
                if rec.get("run_id") == run_id:
                    extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
                    meta = {"question": rec.get("question",""),
                            "ts": rec.get("ts",""), "impl": rec.get("impl",""),
                            "stop_reason": rec.get("stop_reason",""),
                            "tokens": rec.get("tokens", 0),
                            "session_id": extra.get("session_id", "")}
                    break
            except json.JSONDecodeError:
                continue
    return {"events": events, "meta": meta}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    """会话详情：该会话全部轮次（run_id/问题/时间），供前端还原多轮完整时间线。"""
    s = SESSION_STORE.get(session_id)
    if s is None:
        return JSONResponse({"error": "session not found"}, status_code=404)
    return {"session_id": s.id, "created": s.meta.get("created", ""),
            "topic": s.meta.get("topic", ""), "runs": s.meta.get("runs", [])}


@app.get("/api/runs")
def runs_list():
    return {"runs": list(reversed(load_runs()))}

"""
Stage 11：Agent 可注入内核（事件化改造的产物）。

设计原则（依赖注入式重构）：核心决策流程/工具/提示词/护栏**零逻辑改动**，
只把循环里两个写死的 IO 口抽成可注入接口：
  emit(type, **payload)     输出口：CLI 注入 print 渲染；Web 注入 SSE 队列推送
  approver(name, args)      审批口：CLI 注入终端 input；Web 注入"网页按钮+POST"
  stop_check()              中止检查：CLI 恒 False；Web 查中止按钮状态
注入 CLI 实现 = 现在的终端 agent；注入 Web 实现 = GUI 版 agent。

对外：ResearchAgent(emit, approver, stop_check, budget_max, mcp_fs).run(question)
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
import common.tools as tools_mod
from common.llm_client import make_client, call_llm
from common.context import compact_messages
from common.memory import MemoryStore, extract_preferences
from common.guardrails import (Budget, approval_required, ask_human,
                               WRAP_UP_MSG, DANGEROUS_TOOLS)
from common.observability import log_run


def _cli_emit(type: str, **p):
    """CLI 默认渲染：与 Stage 8 终端体验一致。"""
    if type == "memory_inject":
        print(f"[记忆] 注入 {p['count']} 条长期偏好" +
              (f"：\n{p['text']}" if p.get("text") else "（首次会话，暂无）"))
    elif type == "mcp":
        print(f"[MCP] {p['msg']}")
    elif type == "guardrail":
        print(f"[护栏] token 预算: {p['max']} | 危险工具: {p['dangerous']}")
    elif type == "step":
        print(f"    [第{p['step']}步] " +
              (f"点菜: {','.join(p['tools'])}" if p["tools"] else "✍️ 输出最终报告")
              + f" (输入{p['prompt_tokens']}tok)")
    elif type == "tool_result":
        print(f"      ↳ {p['name']} → {str(p['result'])[:120]}")
    elif type == "compact":
        print(f"   ★ 压缩: 上下文 {p['before']}→{p['after']} 字符")
    elif type == "budget":
        print(f"   [护栏] 预算接近上限({p['used']}/{p['max']})，注入收尾指令")
    elif type == "breaker":
        print(f"   [熔断] {p['note']}")
    elif type == "error":
        print(f"   [错误] {p['message']}")
    elif type == "memory_extract":
        print(f"[记忆] 提取到 {p['count']} 条候选: {p['prefs']}")
    elif type == "memory_saved":
        print(f"[记忆] 本次沉淀 {p['added']} 条新偏好")
    elif type == "final":
        print(f"[结算] 预算 {p['used']}/{p['max']} tok | 停止原因: {p['stop_reason']}")


def _cli_approver(tool: str, args_json: str):
    return ask_human(tool, args_json)


def send_report(recipient: str, subject: str) -> str:
    """危险工具示例：模拟对外发送（MVP 不真发）。"""
    return json.dumps({"status": "sent(mock)", "recipient": recipient,
                       "subject": subject}, ensure_ascii=False)


SEND_TOOL = [{
    "type": "function",
    "function": {
        "name": "send_report",
        "description": "把最终研究报告发送到指定邮箱。【危险操作】仅在用户明确要求发送时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "收件人邮箱"},
                "subject": {"type": "string", "description": "邮件主题"},
            },
            "required": ["recipient", "subject"],
        },
    },
}]


CIRCUIT_BREAKER_MSG = ("⚠️ 工具已连续失败多次。禁止再用相同方式尝试，"
                       "立即基于【已有信息】输出阶段性结论（如实标注未获取到的部分），"
                       "不要浪费剩余预算继续尝试。")

FORCED_FINAL_MSG = ("预算已用尽，立即结束调研。请输出《阶段性结题报告》："
                    "1) 已确认的发现（带来源域名）；2) 未能获取的信息；3) 一句话结论。"
                    "不要再调用任何工具。")




def _as_plain(m):
    """pydantic 对象 → 全字段 dict（含 reasoning_content 等所有字段）。"""
    return m if isinstance(m, dict) else m.model_dump()


def _trim_unanswered_tool_calls(messages: list):
    """摘除末尾"点了菜但没执行"的 assistant(tool_calls) 消息（⚠P40 变体）。
    场景：硬停在模型刚点完菜、还没执行回填的时刻——带着这份非法历史
    去做结题调用会 400。语义：那轮点菜作废，历史回到上一个合法状态。"""
    while messages:
        m = messages[-1]
        if isinstance(m, dict):
            role, tcs = m.get("role"), m.get("tool_calls")
        else:
            role, tcs = m.role, m.tool_calls
        if role == "assistant" and tcs:
            messages.pop()
        else:
            break




def _dump_structure(messages: list, rec_hint: str):
    """诊断探针：把消息结构 dump 到 logs/，排查配对类 400。"""
    import json as _json
    out = []
    for i, m in enumerate(messages):
        d = m if isinstance(m, dict) else m.model_dump()
        tcs = d.get("tool_calls")
        out.append({"i": i, "role": d.get("role"),
                    "tool_call_ids": [t["id"] for t in tcs] if tcs else None,
                    "tool_call_id": d.get("tool_call_id"),
                    "content_head": (d.get("content") or "")[:60]})
    p = ROOT / "logs" / f"debug_structure_{rec_hint}_{int(time.time())}.json"
    p.write_text(_json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


class ResearchAgent:
    """生产研究 agent 内核：逻辑与 Stage 8 完全一致，IO 可注入。"""

    _DEFAULT = object()   # 哨兵：区分"未传"与"显式不限"

    def __init__(self, emit=None, approver=None, stop_check=None,
                 budget_max=_DEFAULT, use_mcp=False, impl="agent_core",
                 run_id=None):
        self.emit = emit or _cli_emit
        self.approver = approver or _cli_approver
        self.stop_check = stop_check or (lambda: False)
        if budget_max is self._DEFAULT:   # 未指定 → 默认档（LOW_BUDGET 演示优先）
            budget_max = (3000 if os.getenv("LOW_BUDGET")
                          else config.BUDGET_TIERS.get(config.DEFAULT_BUDGET_TIER, 30000))
        self.budget_max = budget_max      # ★ 必须存回实例：run() 里要用
        # budget_max=None 保持 None → Budget 不限额模式
        self.use_mcp = use_mcp
        self.impl = impl
        self.stop_reason = "model_done"
        self._wrap_injected = False
        self.run_id = run_id   # 外部传入则沿用（Web 工作台），否则自生成

    # ---------- 主流程（逻辑与 Stage 8 逐行对应） ----------
    def _emit_sub(self, type: str, **p):
        """步内子步骤事件：自动编号为 step.sub（如 1.2），供层级视图使用。"""
        self._sub += 1
        p["sub"] = f"{self._step_cur}.{self._sub}"
        self.emit(type, **p)

    def run(self, question: str) -> dict:
        client = make_client()
        self._step_cur, self._sub = 0, 0   # 0 号段 = 启动准备（记忆/护栏/MCP）

        # 记忆注入
        memory = MemoryStore(ROOT / "notes" / "memory.json")
        prefs_text = memory.format_for_prompt()
        self._emit_sub("memory_inject", count=len(memory.data.get("facts", [])),
                       text=prefs_text)

        system_prompt = (
            "你是一个严谨的研究助理。规则：\n"
            "1. 用 web_search/fetch_url 查证，禁止编造；\n"
            "2. 关键发现随手 note_write；收尾前必须 note_read；\n"
            "3. 报告带 [n](url) 引用，简体中文；\n"
            "4. send_report 是危险操作，仅当用户明确要求发送时才使用；\n"
            "5. 收尾标准：当用户问题的核心要点均已有带来源的答案、且无关键信息缺口时，"
            "立即停止调用工具并输出报告——不要为了完备而过度调研。\n"
            + (prefs_text + "\n" if prefs_text else "")
        )

        # 工具装配
        tools_mod.set_notes_file(ROOT / "notes" / "agent_memory" /
                                 f"run_{int(time.time())}.md")
        tools_mod.REGISTRY.clear()
        tools_mod.REGISTRY.update(tools_mod.REAL_REGISTRY)
        tools_mod.REGISTRY["send_report"] = send_report
        all_tools = tools_mod.REAL_TOOLS + SEND_TOOL

        self.mcp_clients = []
        if self.use_mcp:
            for spec in config.MCP_SERVERS:   # 多 server：逐个启动，工具全合并
                self.mcp_clients.append(self._setup_mcp(spec))
            for c in self.mcp_clients:
                all_tools += c.openai_tools()
                for t in c.openai_tools():
                    n = t["function"]["name"]
                    if any(k in n for k in ("write", "edit", "move", "create")):
                        DANGEROUS_TOOLS[n] = "外部MCP写操作：会修改本地文件"
            total = sum(len(c.openai_tools()) for c in self.mcp_clients)
            self._emit_sub("mcp", msg=f"已接入 {len(self.mcp_clients)} 个 MCP server，"
                                      f"新增 {total} 个工具（写类已标危险级）")

        budget = Budget(self.budget_max)
        self._emit_sub("guardrail", max=budget.max,
                       dangerous=list(DANGEROUS_TOOLS))

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]
        stopped_reason = "model_done"
        t_run = time.time()
        tool_sequence, errors = [], []
        budget_warned = False
        fail_streak, breaker_trips = 0, 0   # 失败熔断器：连败N次强制改道
        step = 0
        final = ""

        for step in range(1, config.MAX_LOOP_STEPS + 1):
            self._step_cur, self._sub = step, 0   # 每步重置子序号
            # ★ 进度状态条：让模型感知步数与预算水位（此前这两个维度它完全感知不到，
            #   导致前期漫无节制、后期被死线突然叫停）。每轮注入一行，成本可忽略。
            if budget.max is not None:
                status = (f"[系统进度] 第 {step}/{config.MAX_LOOP_STEPS} 步 · "
                          f"token 已用 {budget.used}/{budget.max}")
                if budget.near_limit(0.8):
                    status += " · 预算偏紧：请收敛调研范围，优先补关键缺口"
            else:
                status = f"[系统进度] 第 {step}/{config.MAX_LOOP_STEPS} 步 · 预算不限"
            self._emit_sub("progress", text=status)
            messages.append({"role": "user", "content": status})
            # 中止检查（用户点了⏹）：注入一次收尾指令
            if self.stop_check() and not self._wrap_injected:
                self._wrap_injected = True
                stopped_reason = "user_stop"
                messages.append({"role": "user", "content": WRAP_UP_MSG})
                self._emit_sub("budget", used=budget.used, max=budget.max,
                               note="用户请求中止，注入收尾指令")
            if step == config.MAX_LOOP_STEPS - 1:
                messages.append({"role": "user", "content":
                    "⚠️ 时间将尽：note_read 后输出最终报告，不要再搜索。"})
                self._emit_sub("inject", note="步数死线：注入最终报告指令")
            if budget.near_limit() and not budget_warned:
                budget_warned = True
                messages.append({"role": "user", "content": WRAP_UP_MSG})
                self._emit_sub("budget", used=budget.used, max=budget.max,
                               note="预算接近上限，注入收尾指令")

            t_model = time.time()
            model_input = [_as_plain(m) for m in messages]   # ★ 完整输入快照（发给模型的全部内容）
            try:
                msg, usage = call_llm(client, messages, tools=all_tools)
            except Exception as e:
                # 协议层报错（如 400 配对错误）不应炸掉整个 run：
                # 记录错误、置错误停止原因，交给循环外的强制结题兜底
                errors.append(f"call_llm: {type(e).__name__}: {str(e)[:200]}")
                self.emit("error", message=str(e)[:200])
                stopped_reason = "error"
                break
            budget.add(usage)
            messages.append(msg)
            model_ms = round((time.time() - t_model) * 1000)
            # 详单：模型的"输入"（本轮消息规模与token明细）与"输出"（决策内容）
            cached = getattr(getattr(usage, "prompt_tokens_details", None),
                             "cached_tokens", None)
            reasoning = getattr(getattr(usage, "completion_tokens_details", None),
                                "reasoning_tokens", None)
            self._emit_sub("step", step=step,
                      tools=[tc.function.name for tc in (msg.tool_calls or [])],
                      prompt_tokens=usage.prompt_tokens,
                      model_ms=model_ms, total_messages=len(model_input),
                      detail={
                          "model_input": model_input,          # ★ 完整 messages（零删减）
                          "model_output": msg.model_dump(),     # ★ 模型返回的全字段
                          "token_detail": {"prompt": usage.prompt_tokens,
                                           "completion": usage.completion_tokens,
                                           "cached": cached,
                                           "reasoning": reasoning}})

            if not msg.tool_calls:
                final = msg.content or ""
                break

            # ★ 硬收尾：预算到 95% 或耗尽时"拔掉工具箱"——
            #   最后一次调用不带 tools 参数，模型物理上无法再点菜，
            #   只能输出结题报告（软提醒"请求它收尾"实测会被无视，硬拔才有效）
            if budget.exhausted or budget.near_limit(0.95):
                _trim_unanswered_tool_calls(messages)   # ⚠P40：先恢复协议合法
                if config.DEBUG_DUMP:
                    _dump_structure(messages, rec_hint="hard_stop")
                messages.append({"role": "user", "content": FORCED_FINAL_MSG})
                self.emit("budget", used=budget.used, max=budget.max,
                          note="预算达硬线，移除工具强制结题")
                try:
                    fmsg, fusage = call_llm(client, messages)  # ★ 无 tools：不可能再点菜
                    budget.add(fusage)
                    final = fmsg.content or final
                except Exception as e:
                    errors.append(f"budget_hard_stop: {type(e).__name__}: {str(e)[:150]}")
                stopped_reason = "budget_hard_stop"
                break

            if usage.prompt_tokens > config.MAX_CONTEXT_TOKENS:
                before = len(json.dumps(messages, ensure_ascii=False, default=str))
                messages, summary, cstats = compact_messages(client, messages)
                after = len(json.dumps(messages, ensure_ascii=False, default=str))
                self._emit_sub("compact", before=before, after=after, stats=cstats,
                               summary=summary)

            backfill_items = []   # 本轮实际追加的 tool 消息（供回填颗粒详单）
            for tc in msg.tool_calls:
                tool_sequence.append(tc.function.name)
                args = json.loads(tc.function.arguments)

                if approval_required(tc.function.name):  # HITL
                    self._sub += 1
                    self._approval_sub = f"{step}.{self._sub}"
                    self.emit("approval_request", sub=self._approval_sub,
                              tool=tc.function.name,
                              args=tc.function.arguments,
                              reason=DANGEROUS_TOOLS.get(tc.function.name, ""))
                    approved, receipt = self.approver(tc.function.name,
                                                      tc.function.arguments)
                    self.emit("approval_result", sub=self._approval_sub,
                              tool=tc.function.name, approved=approved)
                    receipt_msg = {"role": "tool", "tool_call_id": tc.id,
                                   "content": receipt}
                    messages.append(receipt_msg)
                    backfill_items.append({"tool_call_id": tc.id,
                                           "content": receipt})
                    continue

                t_tool = time.time()
                try:
                    owner = next((c for c in self.mcp_clients
                                  if c.owns(tc.function.name)), None)
                    result = (owner.call_tool(tc.function.name, args)
                              if owner else
                              tools_mod.dispatch(tc.function.name, args))
                except Exception as e:
                    result = f"工具执行异常({type(e).__name__})：{e}"
                    errors.append(f"{tc.function.name}: {e}")
                tool_ms = round((time.time() - t_tool) * 1000)
                if isinstance(result, str) and result.startswith("错误"):
                    fail_streak += 1
                    errors.append(f"{tc.function.name} 连续失败(第{fail_streak}次)")
                else:
                    fail_streak = 0
                self._emit_sub("tool_result", name=tc.function.name,
                               result=str(result)[:200],
                               full_result=str(result),   # 完整输出，不二次截断
                               args=args, tool_ms=tool_ms)
                appended = {"role": "tool", "tool_call_id": tc.id,
                            "content": result}
                messages.append(appended)
                backfill_items.append({"tool_call_id": tc.id,
                                       "content": result})
            # ★ 熔断指令必须在全部 tool 结果回填之后再注入——
            #   插进 assistant(tool_calls) 与 tool 结果之间会破坏配对 → API 400
            if fail_streak >= 4 and breaker_trips < 2:
                breaker_trips += 1
                fail_streak = 0
                messages.append({"role": "user", "content": CIRCUIT_BREAKER_MSG})
                self._emit_sub("breaker", used=budget.used, max=budget.max,
                               note=f"工具连续失败，已注入熔断指令（第{breaker_trips}次）")
            if msg.tool_calls:
                # 回填确认：本轮全部结果已配对入列（协议铁律：一圈一结清）
                self._emit_sub("backfill", count=len(msg.tool_calls),
                               items=backfill_items)
        else:
            final = "（达到最大步数未能完成任务，请缩小问题范围后重试）"
            stopped_reason = "max_steps"

        # ★ 兜底保证：非正常停止时也必须有最终产出（强制无工具结题）
        if stopped_reason in ("budget_hard_stop", "budget_exhausted", "max_steps",
                              "user_stop", "error") and not final:
            _trim_unanswered_tool_calls(messages)   # ⚠P40：先恢复协议合法
            messages.append({"role": "user", "content": FORCED_FINAL_MSG})
            t_fin = time.time()
            try:
                fmsg, fusage = call_llm(client, messages)  # 不带 tools，杜绝再点菜
                budget.add(fusage)
                final = fmsg.content or final
                self.emit("forced_final", ms=round((time.time() - t_fin) * 1000))
            except Exception as e:
                errors.append(f"forced_final: {e}")
            # 最后兜底：结题调用失败时，把笔记原文作为报告输出（信息不能跟着丢）
            if not final:
                notes_p = tools_mod._current_notes_file()
                if notes_p and Path(notes_p).exists():
                    notes_text = Path(notes_p).read_text(encoding="utf-8")
                    if notes_text.strip():
                        final = ("【预算耗尽，自动结题】以下为调研笔记原文"
                                 "（未经整理，数据可信但格式粗糙）：\n\n" + notes_text[:4000])
                final = final or "（预算耗尽且结题失败，未获取到有效结论）"

        # 会话结束：记忆提取 + 运行日志
        transcript = "\n".join(
            f"[{m['role']}] {m.get('content') or ''}"
            for m in messages if isinstance(m, dict))
        t_mem = time.time()
        new_prefs = extract_preferences(client, transcript)
        self.emit("memory_extract", count=len(new_prefs), prefs=new_prefs,
                  transcript=transcript,
                  ms=round((time.time() - t_mem) * 1000))
        added = memory.merge(new_prefs, client=client)
        self.emit("memory_saved", added=added)

        rec = log_run(run_id=self.run_id, impl=self.impl,
                      question=question[:80], steps=step,
                      tool_calls=tool_sequence, tokens=budget.used,
                      budget_max=budget.max, stop_reason=stopped_reason,
                      errors=errors, extra={"new_preferences": added})
        self.emit("final", used=budget.used, max=budget.max,
                  stop_reason=stopped_reason, run_id=rec["run_id"],
                  steps=step, answer=final,
                  total_ms=round((time.time() - t_run) * 1000))

        return {"answer": final, "stop_reason": stopped_reason,
                "steps": step, "tokens": budget.used, "errors": errors,
                "run_id": rec["run_id"]}

    def _setup_mcp(self, spec: dict):
        # 共享单例：同 server 全项目只连一次（修复每次运行 spawn 新进程的泄漏）
        from common.mcp_client import get_shared_client
        mcp = get_shared_client(spec)
        if mcp.error:
            raise RuntimeError(f"MCP server '{spec['name']}' 启动失败: {mcp.error[:300]}")
        return mcp

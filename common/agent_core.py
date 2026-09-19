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
import re
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
from common.session import Session


def _cli_emit(type: str, **p):
    """CLI 默认渲染：与 Stage 8 终端体验一致。"""
    if type == "memory_inject":
        print(f"[记忆] 注入 {p['count']} 条长期偏好" +
              (f"：\n{p['text']}" if p.get("text") else "（首次会话，暂无）"))
    elif type == "mcp":
        print(f"[MCP] {p['msg']}")
    elif type == "guardrail":
        print(f"[护栏] token 预算: {p['max']} | 危险工具: {p['dangerous']}")
    elif type == "plan":
        sub_qs = p.get("sub_questions") or []
        print(f"[规划] 目标: {p.get('goal', '')} ({len(sub_qs)} 个子问题)")
        for sq in sub_qs:
            print(f"      - [{sq.get('id', '')}] {sq.get('q', '')} (查证: {sq.get('what_to_verify', '')})")

    elif type == "step":
        print(f"    [第{p['step']}步] " +
              (f"点菜: {','.join(p['tools'])}" if p["tools"] else "✍️ 输出最终报告")
              + f" (输入{p['prompt_tokens']}tok)")
    elif type == "tool_result":
        hit_mark = " ⚡[命中缓存/去重]" if p.get("cache_hit") else ""
        extract_mark = f" 📄[萃取提纯-{p.get('extract_ratio')}%]" if p.get("extracted") else ""
        fallback_mark = " 🌐[自动降级JS渲染]" if p.get("auto_fallback") else ""
        print(f"      ↳ {p['name']}{hit_mark}{extract_mark}{fallback_mark} → {str(p['result'])[:120]}")
    elif type == "compact":
        print(f"   ★ 压缩: 上下文 {p['before']}→{p['after']} 字符")
    elif type == "budget":
        note = p.get("note") or "预算接近上限，注入收尾指令"
        print(f"   [护栏] {note}（{p['used']}/{p['max'] if p['max'] is not None else '不限'} tok）")
    elif type == "breaker":
        print(f"   [熔断] {p['note']}")
    elif type == "checklist":
        completed = p.get("completed", 0)
        total = p.get("total", 0)
        print(f"   📌 [看板更新] 已核销 {completed}/{total} 个子问题")
        for it in (p.get("items") or []):
            st = "✅" if it["status"] == "completed" else ("⏳" if it["status"] == "in_progress" else "⚪")
            summary_info = f" (结论: {it['summary']})" if it.get("summary") else ""
            print(f"      {st} [子问题{it['id']}] {it['q']}{summary_info}")
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

def _build_forced_final_msg() -> str:
    """构建强制结题指令：由代码主动从磁盘提取调研笔记全文嵌入 Prompt，
    消除模型因'收尾前必须 note_read'而尝试输出裸 DSML 工具调用的动机。"""
    notes_p = tools_mod._current_notes_file()
    notes_text = ""
    if notes_p and Path(notes_p).exists():
        notes_text = Path(notes_p).read_text(encoding="utf-8").strip()

    msg = "【系统指令：预算或步数已达上限，立即终止调研并结题】\n\n"
    if notes_text:
        msg += (
            f"系统已为你自动提取了此前记录的全部调研笔记全文如下：\n"
            f"```markdown\n{notes_text}\n```\n\n"
        )
    else:
        msg += "（此前未记录到有效调研笔记）\n\n"

    msg += (
        "硬性指示：\n"
        "1. 笔记已在上方完整给出，无需且【严禁】调用 note_read 或任何工具；\n"
        "2. 【严禁】输出任何 <｜｜DSML｜｜>、<tool_call> 等标签或调用代码；\n"
        "3. 请直接依据上述笔记，整理并输出《阶段性结题报告》Markdown 全文：\n"
        "   - 已确认的核心事实与数据（严格依据笔记，附带来源链接/域名）；\n"
        "   - 尚未查清的遗留事项与缺口；\n"
        "   - 一句话结论，并在末尾说明若继续研究建议下一步查什么。"
    )
    return msg


def _clean_report(text: str) -> str:
    """清洗报告中的残留模型内部调用标记（如 DeepSeek DSML 标签）。"""
    if not text:
        return ""
    # 清理所有包含 ｜｜ 的标签，例如 <｜｜DSML｜｜ calls>、</｜｜DSML｜｜ invoke>
    t = re.sub(r"<[^>\n]*｜｜[^>\n]*>", "", text)
    # 清理标准的 tool_call 标签
    t = re.sub(r"<[/]?(?:tool_call|invoke)[^>\n]*>", "", t)
    return t.strip()


def _is_invalid_report(text: str) -> bool:
    """检查输出是否为无效报告（例如仅包含 DSML 标签、空文本或工具调用代码片段）。"""
    if not text or not text.strip():
        return True
    clean = _clean_report(text)
    # 若清洗后有效字符不足 30 字，视为无效输出
    return len(clean.strip()) < 30


FORCED_FINAL_MSG = ("预算或步数已用尽，立即结束调研。请基于已有笔记输出《阶段性结题报告》："
                    "1) 已确认的发现（带来源域名）；2) 未能获取的信息；3) 一句话结论；"
                    "4) 末尾用一段话说明'若继续研究，建议下一步查什么'。"
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
                 run_id=None, session=None):
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
        self.session = session   # Session 对象（多轮研究会话）
        self.checklist = []
        self._step_cur = 0
        self._sub = 0

    # ---------- 主流程（逻辑与 Stage 8 逐行对应） ----------
    def _emit_sub(self, type: str, **p):
        """步内子步骤事件：自动编号为 step.sub（如 1.2），供层级视图使用。"""
        self._sub += 1
        p["sub"] = f"{self._step_cur}.{self._sub}"
        self.emit(type, **p)

    def format_checklist_for_prompt(self) -> str:
        """生成供 LLM 当前轮感知的大纲待办核销看板。"""
        if not self.checklist:
            return ""
        lines = ["【研究进度待办看板】"]
        for item in self.checklist:
            mark = "[x]" if item["status"] == "completed" else ("[~]" if item["status"] == "in_progress" else "[ ]")
            status_desc = f"(已完成: {item['summary']})" if item["status"] == "completed" and item["summary"] else (
                "(正在查证...)" if item["status"] == "in_progress" else "(待查证)"
            )
            lines.append(f"- {mark} 子问题 {item['id']}: {item['q']} {status_desc}")

        completed_cnt = sum(1 for it in self.checklist if it["status"] == "completed")
        total_cnt = len(self.checklist)
        if completed_cnt == total_cnt and total_cnt > 0:
            lines.append("\n🎉 所有大纲子问题已全部核销完成！核心事实已充分，请立即 note_read 并输出完整报告，严禁继续发散检索。")
        return "\n".join(lines)

    def update_checklist_item(self, sub_id: int, status: str, summary: str = "") -> str:
        """更新指定子问题的看板状态，并触发 checklist 事件。"""
        for item in self.checklist:
            if str(item["id"]) == str(sub_id):
                item["status"] = status
                if summary:
                    item["summary"] = summary
                completed = sum(1 for it in self.checklist if it["status"] == "completed")
                self._emit_sub("checklist", items=[dict(it) for it in self.checklist],
                               completed=completed, total=len(self.checklist))
                return f"看板更新成功：子问题 {sub_id} 状态已更新为 {status}" + (f"（结论: {summary}）" if summary else "")
        return f"未找到编号为 {sub_id} 的子问题，当前可用子问题编号：{[it['id'] for it in self.checklist]}"

    def sync_checklist_from_note(self, note_content: str):
        """启发式同步：若笔记中提及某子问题且该项尚未完成，自动标记为已完成。"""
        if not self.checklist or not note_content:
            return
        import re
        for item in self.checklist:
            if item["status"] == "completed":
                continue
            sub_id = str(item["id"])
            patterns = [
                rf"【子问题\s*{sub_id}】",
                rf"子问题\s*{sub_id}[：:]",
                rf"子问题\s*{sub_id}\b",
                rf"\[子问题\s*{sub_id}\]"
            ]
            if any(re.search(pat, note_content) for pat in patterns):
                clean_lines = [l.strip() for l in note_content.splitlines() if l.strip()]
                first_line = clean_lines[0] if clean_lines else ""
                first_line = re.sub(r"^[#\-\*\s【】\[\]子问题0-9：:]+", "", first_line).strip()
                summary = first_line[:50] or "已记录相关调研笔记"
                self.update_checklist_item(item["id"], "completed", summary)

    def _session_context(self) -> str:
        """多轮对话上下文：注入 session 的历史发现和对话轮次，让模型知道之前研究了什么。"""
        if not self.session:
            return ""
        ctx = self.session.load_context()
        notes = self.session.read_notes()
        bits = []
        if ctx and ctx.get("summary"):
            bits.append(f"【此前研究摘要】\n{ctx['summary']}")
        if notes.strip():
            bits.append(f"【已有研究笔记】\n{notes[:2000]}")
        if not bits:
            return ""
        return "\n【会话上下文——此前运行的成果，可引用或继续深入】\n" + "\n".join(bits) + "\n"

    def run(self, question: str) -> dict:
        client = make_client()
        self._step_cur, self._sub = 0, 0   # 0 号段 = 启动准备（记忆/护栏/MCP）

        # 记忆注入（相关性门禁召回，通用偏好必留，无关领域偏好排除，杜绝记忆带偏）
        memory = MemoryStore(ROOT / "notes" / "memory.json")
        prefs_text = memory.format_for_prompt(question=question)
        mem_stats = memory.get_stats_for_prompt(question=question)
        self._emit_sub("memory_inject",
                       count=mem_stats["injected"],
                       total=mem_stats["total"],
                       filtered=mem_stats["filtered"],
                       text=prefs_text)

        system_prompt = (
            "你是一个严谨的研究助理。规则：\n"
            "1. 用 web_search/fetch_url 查证，禁止编造；\n"
            "2. 关键发现随手 note_write；收尾前必须 note_read；\n"
            "3. 引用格式（硬性）：报告中每个事实性陈述后必须紧跟 [n](完整URL) 形式的引用编号，"
            "并在报告末尾用'## 参考'小节逐条列出 [n] 完整链接——"
            "严禁只写域名文字代替编号引用；\n"
            "4. send_report 是危险操作，仅当用户明确要求发送时才使用；\n"
            "5. 充分度收敛门禁（硬性）：深度研究不等于无休止穷举！对照【用户问题】，"
            "只要核心主干要点（产品定位、核心工作流/链路、交互入口、权限与安全边界）"
            "已有事实和来源支撑（以笔记为准），即判定为信息充分！"
            "严禁继续发散检索无关的边缘配置、分值折算、详细价目表等次要细节。"
            "一旦主干充分，立即调用 note_read 并输出报告，坚决杜绝为了'追求极致完备'而原地漫游；\n"
            "6. 数字纪律（硬性）：所有数字（价格/限额/日期/规格）必须逐字来自工具返回的原文；"
            "证据中没有的数字一律标注'未验证'，严禁凭记忆、推算或换算补全；\n"
            "7. 摘要优先原则（Snippet-First）：搜索结果返回的 snippet 经常已包含确切日期、版本号、产品定义或核心结论。"
            "若搜索摘要已能证实某事实，可直接调用 note_write 沉淀并带上对应 url 引用，无需盲目打开每一个网页；仅当摘要缺少关键细节时才调用 fetch_url；\n"
            "8. 信源分歧处理（交叉验证）：当不同信源（如官方文档 vs 第三方自媒体/社区讨论）的数据或结论相冲突时，"
            "优先采信一手官方发布；若关键分歧无法简单消除，必须在报告中明确指出'存在信源分歧'，"
            "分别列出各自信源论据及引用链接编号，严禁擅自猜测抹平或平均化折中。\n"
            + (prefs_text + "\n" if prefs_text else "")
            + (self._session_context() if self.session else "")
        )

        # 工具与缓存装配（使用局部字典与独立会话缓存，杜绝全局竞态与重复探索）
        # ★ 笔记续跑：同 session 共用一个笔记文件（跨运行持久化）
        if self.session:
            notes_p = self.session.notes_path
        else:
            notes_p = ROOT / "notes" / "agent_memory" / f"run_{int(time.time())}.md"
        tools_mod.set_notes_file(notes_p)
        from common.cache import SessionToolCache
        self.cache = SessionToolCache()
        self.tools_registry = dict(tools_mod.REAL_REGISTRY)
        self.tools_registry["send_report"] = send_report
        self.tools_registry["update_checklist"] = self.update_checklist_item
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

        t_run = time.time()
        tool_sequence, errors = [], []

        # 节点：规划（JSON mode 生成研究大纲，计入 Budget 且返回 outline）
        outline = {}
        t_plan = time.time()
        plan_messages = [
            {"role": "system",
             "content": "你是研究规划师。针对用户问题输出JSON研究大纲，格式："
                        '{"goal":"一句话目标","sub_questions":[{"id":1,"q":"子问题",'
                        '"what_to_verify":"要查证什么"}],"constraints":["约束"]}。'
                        "硬性要求：子问题不超过3个、what_to_verify一句话以内——"
                        "大纲必须在约10步内可调研完成，贪多会导致任务永远做不完。只规划，不执行。"},
            {"role": "user", "content": question},
        ]
        try:
            plan_msg, plan_usage = call_llm(
                client,
                messages=plan_messages,
                response_format={"type": "json_object"},
            )
            budget.add(plan_usage)
            outline = json.loads(plan_msg.content or "{}")
            # ★详单与 step 节点同构：完整输入 / 全字段输出 / token 明细（Web 端全量展示）
            p_cached = getattr(getattr(plan_usage, "prompt_tokens_details", None),
                               "cached_tokens", None)
            p_reasoning = getattr(getattr(plan_usage, "completion_tokens_details", None),
                                  "reasoning_tokens", None)
            self._emit_sub("plan", outline=outline, goal=outline.get("goal", ""),
                           sub_questions=outline.get("sub_questions", []),
                           ms=round((time.time() - t_plan) * 1000),
                           prompt_tokens=plan_usage.prompt_tokens,
                           detail={
                               "model_input": {"messages": plan_messages},  # 规划调用无 tools：规划师只拆问题
                               "model_output": plan_msg.model_dump(),
                               "token_detail": {"prompt": plan_usage.prompt_tokens,
                                                "completion": plan_usage.completion_tokens,
                                                "cached": p_cached,
                                                "reasoning": p_reasoning}})
        except Exception as e:
            errors.append(f"plan: {type(e).__name__}: {str(e)[:150]}")
            outline = {"goal": question, "sub_questions": [], "constraints": []}

        sub_qs = outline.get("sub_questions", [])
        self.checklist = [
            {
                "id": sq.get("id", i + 1),
                "q": sq.get("q", ""),
                "what_to_verify": sq.get("what_to_verify", ""),
                "status": "pending",
                "summary": ""
            }
            for i, sq in enumerate(sub_qs)
        ]

        if outline and outline.get("sub_questions"):
            outline_text = json.dumps(outline, ensure_ascii=False)
            system_prompt += f"\n\n【研究大纲】\n{outline_text}\n请对照大纲中的子问题与查证要点逐步执行调研。"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]
        stopped_reason = "model_done"
        evidence = []   # 检索/抓取的原始结果（供评测 judge 核对有据性）
        budget_warned = False
        fail_streak, breaker_trips = 0, 0   # 失败熔断器：连败N次强制改道
        step = 0
        final = ""


        for step in range(1, config.MAX_LOOP_STEPS + 1):
            self._step_cur, self._sub = step, 0   # 每步重置子序号
            # ★ 进度状态条与动态看板：让模型感知步数与预算水位，并动态感知大纲核销进度
            all_completed = (
                len(self.checklist) > 0 and
                all(it.get("status") == "completed" for it in self.checklist)
            )
            if budget.max is not None:
                status = (f"[系统进度] 第 {step}/{config.MAX_LOOP_STEPS} 步 · "
                          f"token 已用 {budget.used}/{budget.max}")
                if all_completed:
                    status += " · 🎉 [全量核销] 所有大纲子问题已全部核销完成！核心事实已充分，请立即 note_read 并输出报告，严禁继续发散检索！"
                elif budget.near_limit(0.8):
                    status += " · ⚠️ 预算偏紧：请收敛调研范围，优先补关键缺口并准备结题"
                elif step >= 4 or (budget.used >= 0.5 * budget.max):
                    status += " · [进展自检] 若核心主干事实已充分，请立即收尾出报告，避免发散到非核心细节"
            else:
                status = f"[系统进度] 第 {step}/{config.MAX_LOOP_STEPS} 步 · 预算不限"
                if all_completed:
                    status += " · 🎉 [全量核销] 所有大纲子问题已全部核销完成！核心事实已充分，请立即 note_read 并输出报告，严禁继续发散检索！"
                elif step >= 4:
                    status += " · [进展自检] 若核心主干事实已充分，请立即收尾出报告，避免发散到非核心细节"

            cl_text = self.format_checklist_for_prompt()
            full_status = status + (f"\n\n{cl_text}" if cl_text else "")
            self._emit_sub("progress", text=status, checklist=cl_text)
            messages.append({"role": "user", "content": full_status})
            # 中止检查（用户点了⏹）：注入一次收尾指令
            if self.stop_check() and not self._wrap_injected:
                self._wrap_injected = True
                stopped_reason = "user_stop"
                messages.append({"role": "user", "content": WRAP_UP_MSG})
                self._emit_sub("budget", used=budget.used, max=budget.max,
                               note="用户请求中止，注入收尾指令")
            if step >= config.MAX_LOOP_STEPS - 1:
                messages.append({"role": "user", "content":
                    "⚠️ 时间将尽：note_read 后输出最终报告，不要再搜索。"})
                self._emit_sub("inject", note="步数死线：注入最终报告指令")
            if budget.near_limit() and not budget_warned:
                budget_warned = True
                messages.append({"role": "user", "content": WRAP_UP_MSG})
                self._emit_sub("budget", used=budget.used, max=budget.max,
                               note="预算接近上限，注入收尾指令")

            t_model = time.time()
            model_input = {"messages": [_as_plain(m) for m in messages],   # ★ 完整输入快照：对话历史
                           "tools": all_tools}                              # ★ 工具说明书（模型点菜的依据）
            n_msg = len(model_input["messages"])
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
                      model_ms=model_ms, total_messages=n_msg,
                      detail={
                          "model_input": model_input,          # ★ 完整请求体（messages+tools，零删减）
                          "model_output": msg.model_dump(),     # ★ 模型返回的全字段
                          "token_detail": {"prompt": usage.prompt_tokens,
                                           "completion": usage.completion_tokens,
                                           "cached": cached,
                                           "reasoning": reasoning}})

            if not msg.tool_calls:
                candidate = msg.content or ""
                if not _is_invalid_report(candidate):
                    final = _clean_report(candidate)
                    break
                # 若无 tool_calls 且正文被判定为无效标签，不跳出，继续向下走收尾/报错

            # ★ 硬收尾：预算到 95% 或耗尽时"拔掉工具箱"——
            #   最后一次调用不带 tools 参数，模型物理上无法再点菜，
            #   由代码主动从磁盘提取调研笔记全文嵌入 Prompt，杜绝模型输出裸 DSML
            if budget.exhausted or budget.near_limit(0.95):
                _trim_unanswered_tool_calls(messages)   # ⚠P40：先恢复协议合法
                if config.DEBUG_DUMP:
                    _dump_structure(messages, rec_hint="hard_stop")
                forced_msg = _build_forced_final_msg()
                messages.append({"role": "user", "content": forced_msg})
                self._emit_sub("budget", used=budget.used, max=budget.max,
                               note="预算达硬线，移除工具强制结题")   # ★带sub：前端addSub依赖层级编号
                t_fin = time.time()  # ★ 修复：提前记录 t_fin，避免 except 分支报 NameError
                try:
                    fmsg, fusage = call_llm(client, messages)  # ★ 无 tools：不可能再点菜
                    budget.add(fusage)
                    candidate = fmsg.content or ""
                    if not _is_invalid_report(candidate):
                        final = _clean_report(candidate)
                except Exception as e:
                    errors.append(f"budget_hard_stop: {type(e).__name__}: {str(e)[:150]}")
                    self.emit("forced_final", ms=round((time.time() - t_fin) * 1000),
                              output="", error=f"{type(e).__name__}: {str(e)[:150]}")
                stopped_reason = "budget_hard_stop"
                break


            if usage.prompt_tokens > config.MAX_CONTEXT_TOKENS:
                before = len(json.dumps(messages, ensure_ascii=False, default=str))
                messages, summary, cstats = compact_messages(client, messages)
                if cstats and cstats.get("usage"):
                    budget.add(cstats["usage"])
                after = len(json.dumps(messages, ensure_ascii=False, default=str))
                self._emit_sub("compact", before=before, after=after, stats=cstats,
                               summary=summary)


            backfill_items = []   # 本轮实际追加的 tool 消息（供回填颗粒详单）
            for tc in (msg.tool_calls or []):
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
                              tool=tc.function.name, approved=approved,
                              receipt=receipt)
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
                              tools_mod.dispatch(tc.function.name, args,
                                                 registry=self.tools_registry,
                                                 cache=self.cache,
                                                 step=step,
                                                 extract_client=client,
                                                 goal=question,
                                                 on_extract_usage=budget.add))

                except Exception as e:
                    result = f"工具执行异常({type(e).__name__})：{e}"
                    errors.append(f"{tc.function.name}: {e}")
                tool_ms = round((time.time() - t_tool) * 1000)
                if isinstance(result, str) and result.startswith("错误"):
                    fail_streak += 1
                    errors.append(f"{tc.function.name} 连续失败(第{fail_streak}次)")
                else:
                    fail_streak = 0
                if tc.function.name == "note_write":
                    self.sync_checklist_from_note(args.get("content", ""))
                if tc.function.name in ("web_search", "fetch_url", "fetch_js"):
                    evidence.append(str(result)[:1000])

                cache_hit = bool(getattr(self.cache, "last_hit", False))
                cache_type = getattr(self.cache, "last_hit_type", None)
                extracted = bool(getattr(self.cache, "last_extracted", False))
                extract_ratio = getattr(self.cache, "last_extract_ratio", 0)
                before_chars = getattr(self.cache, "last_raw_chars", 0)
                after_chars = getattr(self.cache, "last_extracted_chars", 0)
                auto_fallback = bool(getattr(self.cache, "last_auto_fallback", False))

                self._emit_sub("tool_result", name=tc.function.name,
                               result=str(result)[:200],
                               full_result=str(result),   # 完整输出，不二次截断
                               args=args, tool_ms=tool_ms,
                               cache_hit=cache_hit, cache_type=cache_type,
                               extracted=extracted, extract_ratio=extract_ratio,
                               before_chars=before_chars, after_chars=after_chars,
                               auto_fallback=auto_fallback)
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
            # ★ 步数耗尽：不丢占位符——强制无工具收尾（同预算硬收尾路径）：
            #   摘除未应答点菜（协议合法）→ 带笔记输出《阶段性结题报告》
            stopped_reason = "max_steps"
            _trim_unanswered_tool_calls(messages)   # ⚠P40：末尾未应答点菜作废
            forced_msg = _build_forced_final_msg()
            messages.append({"role": "user", "content": forced_msg})
            self._emit_sub("budget", used=budget.used,
                           max=(budget.max if budget.max is not None else 0),
                           note="步数耗尽：强制无工具结题")   # ★带sub：前端addSub依赖层级编号
            t_fin = time.time()
            try:
                fmsg, fusage = call_llm(client, messages)   # 无 tools：不可能再点菜
                budget.add(fusage)
                candidate = fmsg.content or ""
                if not _is_invalid_report(candidate):
                    final = _clean_report(candidate)
                self.emit("forced_final", ms=round((time.time() - t_fin) * 1000),
                          output=(final or "")[:400])
            except Exception as e:
                errors.append(f"max_steps_forced: {type(e).__name__}: {str(e)[:150]}")

        # ★ 兜底保证：非正常停止时也必须有最终产出（强制无工具结题）
        if stopped_reason in ("budget_hard_stop", "budget_exhausted", "max_steps",
                              "user_stop", "error") and not final:
            _trim_unanswered_tool_calls(messages)   # ⚠P40：先恢复协议合法
            forced_msg = _build_forced_final_msg()
            messages.append({"role": "user", "content": forced_msg})
            t_fin = time.time()
            try:
                fmsg, fusage = call_llm(client, messages)  # 不带 tools，杜绝再点菜
                budget.add(fusage)
                candidate = fmsg.content or ""
                if not _is_invalid_report(candidate):
                    final = _clean_report(candidate)
                self.emit("forced_final", ms=round((time.time() - t_fin) * 1000),
                          output=(final or "")[:400],
                          input_note="系统强制结题")
            except Exception as e:
                errors.append(f"forced_final: {e}")

        # 最后兜底：结题调用失败或输出被判定为无效标签时，把笔记原文作为报告输出（信息不能跟着丢）
        if not final or _is_invalid_report(final):
            notes_p = tools_mod._current_notes_file()
            if notes_p and Path(notes_p).exists():
                notes_text = Path(notes_p).read_text(encoding="utf-8")
                if notes_text.strip():
                    final = ("【自动结题报告（基于调研笔记）】以下为调研笔记原文"
                             "（已自动提取核心数据）：\n\n" + notes_text.strip()[:6000])
            final = final or "（预算耗尽且结题失败，未获取到有效结论）"
        else:
            final = _clean_report(final)


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
        self.emit("memory_saved", added=added,
                  facts=[f["text"] for f in memory.data["facts"]])

        # 多轮对话：保存上下文快照（摘要/发现/待办）供下次运行注入
        if self.session:
            key_findings = []
            pending = []
            for m in messages:
                d = m if isinstance(m, dict) else m.model_dump()
                if d.get("role") == "assistant" and d.get("tool_calls"):
                    for tc in d["tool_calls"]:
                        if tc["function"]["name"] == "note_write":
                            try:
                                key_findings.append(json.loads(tc["function"]["arguments"]).get("content", "")[:200])
                            except Exception:
                                pass
            self.session.save_context(
                summary=f"已完成 {step} 步研究，提取到 {len(self.session.read_notes())} 字符笔记",
                key_findings=key_findings[-5:], pending=pending)
            # ★运行注册由 webapp 在启动时完成（app.py session.add_run），
            #   这里不再重复注册——否则会话历史每个 run 记两条，
            #   且此时 rec 尚未定义（log_run 在下方才调用），run_id 为空时会 NameError

        rec = log_run(run_id=self.run_id, impl=self.impl,
                      question=question[:80], steps=step,
                      tool_calls=tool_sequence, tokens=budget.used,
                      budget_max=budget.max, stop_reason=stopped_reason,
                      errors=errors,
                      extra={"new_preferences": added,
                             "session_id": (self.session.id if self.session else "")})
        self.emit("final", used=budget.used, max=budget.max,
                  stop_reason=stopped_reason, run_id=rec["run_id"],
                  steps=step, answer=final,
                  total_ms=round((time.time() - t_run) * 1000))

        return {"answer": final, "stop_reason": stopped_reason,
                "steps": step, "tokens": budget.used, "errors": errors,
                "evidence": evidence, "run_id": rec["run_id"],
                "outline": outline,
                "saved_calls": getattr(self.cache, "total_saved_calls", 0)}


    def _setup_mcp(self, spec: dict):
        # 共享单例：同 server 全项目只连一次（修复每次运行 spawn 新进程的泄漏）
        from common.mcp_client import get_shared_client
        mcp = get_shared_client(spec)
        if mcp.error:
            raise RuntimeError(f"MCP server '{spec['name']}' 启动失败: {mcp.error[:300]}")
        return mcp

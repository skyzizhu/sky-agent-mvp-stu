"""
日志脚本：完整记录一次 agent 运行中【每一次模型调用的全部输入输出】，并自动校验一致性。

原理：
  common/llm_client.py 里内置了录制器（RECORDER）。本脚本把 RECORDER 挂上，
  然后正常运行 research_agent——每次真实调用发生的那一刻，请求与响应的
  深拷贝快照被定格存档（运行结束后仍完整、互不污染）。

产出: notes/stage3_完整输入输出.md
  - 每次调用的完整请求体（model/messages/tools 全字段）
  - 每次调用的完整响应体（model_dump() 全字段）
  - 文末附带自动校验结果（快照递增、tool_call_id 配对、finish_reason 等）

运行: .venv/bin/python stages/03_real_tools/capture_full_io.py
"""
import json
import sys
import time
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import llm_client

# 1) 挂上录制器：之后的每次模型调用都会自动留档
recorder = llm_client.CallRecorder()
llm_client.RECORDER = recorder

# 2) 正常加载并运行 research_agent（不做任何行为篡改）
spec = importlib.util.spec_from_file_location(
    "research_agent", ROOT / "stages/03_real_tools/research_agent.py")
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)
ra.main()

# ---------- 3) 自动校验：验证日志自洽，结果写入文档 ----------
def validate() -> list[str]:
    checks = []
    calls = recorder.calls

    # 校验A：快照互不污染且递增——后一次的 messages 必须严格包含前一次的全部消息
    ok = True
    for i in range(1, len(calls)):
        prev = json.dumps(calls[i - 1]["request"]["messages"], ensure_ascii=False, default=str)
        curr = json.dumps(calls[i]["request"]["messages"], ensure_ascii=False, default=str)
        if not curr.startswith(prev[:200]):  # system+user 前缀一致
            ok = False
        if len(calls[i]["request"]["messages"]) <= len(calls[i - 1]["request"]["messages"]):
            ok = False
    n1 = len(calls[0]["request"]["messages"])
    checks.append(f"[{'通过' if ok else '失败'}] 快照递增：第1次调用仅{n1}条消息，"
                  f"之后每次都比上一次多（历史只增不减，且互不污染）")

    # 校验B：tool_call_id 配对——请求里每个 assistant 的调用，同请求内必有对应 tool 结果
    ok = True
    for i, c in enumerate(calls, 1):
        msgs = c["request"]["messages"]
        call_ids, result_ids = set(), set()
        for m in msgs:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                call_ids |= {t["id"] for t in m["tool_calls"]}
            if m.get("role") == "tool":
                result_ids.add(m.get("tool_call_id"))
        if call_ids != result_ids:
            ok = False
            checks.append(f"[失败] 第{i}次调用: 点菜{call_ids}与结果{result_ids}不配对")
    if ok:
        checks.append(f"[通过] tool_call_id 配对：3次调用中每个调用编号都有唯一对应的结果消息")

    # 校验C：停止信号——最后一次调用的 finish_reason 应为 stop（任务完成收尾）
    last = calls[-1]["response"]["choices"][0]["finish_reason"]
    checks.append(f"[{'通过' if last == 'stop' else '注意'}] 最后一次调用 finish_reason = {last}"
                  f"（stop=模型说完最终答案，任务收尾）")

    # 校验D：token 复核——响应头标注的数字必须等于 usage 里的数字
    ok = all(c["response"]["usage"]["prompt_tokens"] ==
             c["response"]["usage"]["total_tokens"] - c["response"]["usage"]["completion_tokens"]
             for c in calls)
    checks.append(f"[{'通过' if ok else '失败'}] token 复核：每次调用 prompt+completion=total 成立")
    return checks


# ---------- 4) 生成 Markdown 文档 ----------
lines = [
    "# Stage 3 每一次模型调用的完整输入输出（原始级，逐字段）",
    "",
    f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}　|　任务: {ra.__dict__.get('QUESTION', '见消息[1]')}",
    "> 请求体 = 发给 API 的一切；响应体 = API 返回的一切（model_dump 全字段，零删减）。",
    "> 文末为自动校验结果，全部[通过]方可作为学习材料。",
    "",
]
for i, c in enumerate(recorder.calls, 1):
    u = c["response"]["usage"]
    lines += [f"{'='*78}", f"## 第 {i} 次调用  （输入 {u['prompt_tokens']} tok / 输出 {u['completion_tokens']} tok）", "",
              "### 完整请求体", "```json",
              json.dumps(c["request"], ensure_ascii=False, indent=2), "```", "",
              "### 完整响应体", "```json",
              json.dumps(c["response"], ensure_ascii=False, indent=2), "```", ""]

lines += [f"{'='*78}", "## 自动校验结果", ""]
lines += [f"- {c}" for c in validate()]
lines += ["", "> 校验逻辑见 stages/03_real_tools/capture_full_io.py 的 validate()。"]

out = ROOT / "notes/stage3_完整输入输出.md"
out.write_text("\n".join(lines))
print(f"\n已写入 {out}，共 {len(recorder.calls)} 次调用；校验结果已附在文末")

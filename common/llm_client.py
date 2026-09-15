"""
公共模块：LLM 客户端 + Trace 日志。

为什么要单独抽出来？
- 所有阶段都用同一个"发请求"的入口，以后换模型/加参数只改一处
- Trace 日志是 Stage 9 可观测性的地基：agent 调试的对象不是代码 bug，
  而是"模型的决策序列"，所以每一步都要能完整回放
"""
import copy
import json
import time
from pathlib import Path

from openai import OpenAI

import config

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 全局录制器：默认 None（不录制）。挂上 CallRecorder 实例后，每次模型调用自动留档。
RECORDER: "CallRecorder | None" = None


def make_client() -> OpenAI:
    """创建一个 OpenAI 兼容客户端。三个配置项全部来自 .env。"""
    if not config.API_KEY:
        raise RuntimeError(
            "未配置 API key：请复制 .env.example 为 .env 并填入 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL"
        )
    return OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)


def call_llm(client: OpenAI, messages: list, tools: list | None = None, **extra):
    """
    最小封装的"模型调用节点"。所有 LLM 调用都应走这里——
    统一入口才能统一观测（RECORDER 只在这里生效）和统一改配置。

    输入: messages（完整对话历史）, tools（工具声明，可选）, extra（temperature/
          response_format 等额外参数，透传给 API）
    输出: assistant message 对象（可能含 content、也可能含 tool_calls）+ usage
    注意: 每次调用都把【全部历史】重发一遍——这是理解上下文成本的关键
    """
    kwargs = dict(model=config.MODEL, messages=messages, **extra)
    if tools:
        kwargs["tools"] = tools
    resp = client.chat.completions.create(**kwargs)
    # 内建录制：若挂了 RECORDER，把"此刻"的请求与完整响应定格存档。
    # 必须 deepcopy——messages 是引用，loop 会继续往里 append，
    # 不拷贝的话所有记录都会"变成"最后一次调用的样子（共享可变状态的经典bug）。
    if RECORDER is not None:
        RECORDER.record(copy.deepcopy(messages), copy.deepcopy(tools),
                        resp.model_dump())
    return resp.choices[0].message, resp.usage


class CallRecorder:
    """逐次记录每次模型调用的完整请求/响应（原始级，零删减）。"""

    def __init__(self):
        self.calls: list[dict] = []

    @staticmethod
    def _to_plain(obj):
        """把 pydantic 对象转成纯 dict，保证录制内容可 JSON 化。"""
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        return obj

    def record(self, messages: list, tools: list | None, response: dict):
        messages = [self._to_plain(m) if not isinstance(m, dict) else m
                    for m in messages]
        self.calls.append({
            "request": {"model": config.MODEL, "messages": messages,
                        "tools": tools},
            "response": response,
        })


def save_trace(name: str, messages: list, extra: dict | None = None, tools: list | None = None):
    """把一次运行的完整请求/响应落盘。注意：tools 是请求的一部分，必须一起存，
    否则 trace 不能完整回放模型当时"看到"的世界（工具说明书直接影响它的决策）。"""
    traces_dir = PROJECT_ROOT / "traces"
    traces_dir.mkdir(exist_ok=True)
    path = traces_dir / f"{time.strftime('%m%d_%H%M%S')}_{name}.json"
    payload = {"model": config.MODEL, "messages": serialize(messages)}
    if tools:
        payload["tools"] = tools  # 原样保存工具说明书
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n[trace] 已保存: {path}")


def serialize(messages: list) -> list:
    """把 messages 里 openai 的对象转成纯 JSON（pydantic 对象不能直接 dump）。"""
    out = []
    for m in messages:
        if isinstance(m, dict):
            out.append(m)
        else:
            # assistant message 对象 → dict
            out.append({
                "role": "assistant",
                "content": m.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (m.tool_calls or [])
                ] or None,
            })
    return out


def show_step(step: int, msg, result: str | None = None):
    """在终端打印每一步的决策过程（Anthropic 三原则之二：透明化 planning）。"""
    print(f"\n{'='*60}\n[第 {step} 步]")
    if msg.content:
        print(f"🤖 模型输出: {msg.content[:300]}")
    for tc in getattr(msg, "tool_calls", None) or []:
        print(f"🔧 调用工具: {tc.function.name}({tc.function.arguments})")
    if result is not None:
        print(f"📋 工具结果: {result[:300]}")

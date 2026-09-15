"""
LLM Playground 后端：一个"透明代理"。
职责只有一个——把你表单里构造的请求原样发给模型 API，再把 API 返回的
原文一字不落地回显给你。不做任何解析、格式化、纠错——报错原文也是学习材料。

运行: .venv/bin/python -m uvicorn playground.app:app --port 7860
打开: http://127.0.0.1:7860
"""
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse

load_dotenv()
import os

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="LLM Playground")

DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
DEFAULT_KEY = os.getenv("LLM_API_KEY", "")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "deepseek-flash")

# 允许出现在请求体里的可选参数（白名单：之外的一律丢弃，防误传）
OPTIONAL_PARAMS = ["temperature", "top_p", "max_tokens", "presence_penalty",
                   "frequency_penalty", "seed", "stop", "logprobs"]


@app.get("/")
def index():
    return FileResponse(ROOT / "playground" / "static" / "index.html")


@app.get("/api/config")
def config():
    """给前端提供默认值（key 不下发给浏览器）。"""
    return {"model": DEFAULT_MODEL, "base_url": DEFAULT_BASE_URL,
            "has_key": bool(DEFAULT_KEY)}


@app.post("/api/chat")
async def chat(payload: dict):
    """
    输入: 前端表单构造的完整请求意图
    输出: {"request_sent": 实际发出的请求体, "status": HTTP状态码,
           "raw": API返回的原始文本（一字未动）, "elapsed_ms": 耗时}
    """
    messages = payload.get("messages") or []
    request = {"model": payload.get("model") or DEFAULT_MODEL,
               "messages": messages}

    tools_raw = (payload.get("tools") or "").strip()
    if tools_raw:
        request["tools"] = json_loads_or_raw(tools_raw)
    for key in OPTIONAL_PARAMS:
        val = payload.get(key)
        if val not in (None, ""):
            request[key] = maybe_number(val)
    rf = payload.get("response_format")
    if rf:
        request["response_format"] = {"type": rf}
    tc = payload.get("tool_choice")
    if tc:
        request["tool_choice"] = tc

    # ---- 思考模式（DeepSeek OpenAI格式协议）----
    # 开启: "thinking":{"type":"enabled"} + 可选 reasoning_effort: low/high/max
    # 关闭: "thinking":{"type":"disabled"}
    # 不设置: 请求体不含该字段，走 API 默认（默认开启、effort=high）
    tm = payload.get("thinking_mode")
    if tm == "enabled":
        request["thinking"] = {"type": "enabled"}
        effort = payload.get("reasoning_effort")
        if effort:
            request["reasoning_effort"] = effort
    elif tm == "disabled":
        request["thinking"] = {"type": "disabled"}

    base_url = (payload.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {payload.get('api_key') or DEFAULT_KEY}",
               "Content-Type": "application/json"}

    import time
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{base_url}/chat/completions",
                                     headers=headers, json=request)
        return {"request_sent": request, "status": resp.status_code,
                "raw": resp.text, "elapsed_ms": round((time.time() - t0) * 1000)}
    except httpx.HTTPError as e:
        # 网络层错误也原样回显——学习材料包括失败
        return {"request_sent": request, "status": 0,
                "raw": f"网络层错误: {type(e).__name__}: {e}",
                "elapsed_ms": round((time.time() - t0) * 1000)}


def json_loads_or_raw(text: str):
    """tools 文本框里的内容：能解析成JSON就用解析值，否则原样发送（让API报错，也是学习）。"""
    import json
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def maybe_number(val):
    """temperature/max_tokens 等表单传来的字符串转数字。"""
    if isinstance(val, str):
        s = val.strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return val
    return val

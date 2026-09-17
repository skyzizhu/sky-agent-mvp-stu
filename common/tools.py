"""
公共工具箱：Stage 1 定义的声明式工具 + 执行分发器，Stage 2 起的所有阶段复用。

设计要点（对应 Anthropic《Writing Effective Tools》）:
- 少而 consolidated：一个工具完成一件事，不搞 API 端点的薄包装
- description 是写给模型看的文档：何时用、参数含义、边界
- 返回 JSON 字符串且信息"高信号"；错误信息要"可行动"，告诉模型下一步怎么办
"""
import json

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市当前的天气。当用户问天气相关问题时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如：北京"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "计算一个四则运算表达式的值，仅支持 + - * / 和括号。"
                           "当需要比较、求差等算术运算时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "表达式，如 (3+4)*2"},
                },
                "required": ["expression"],
            },
        },
    },
]


def get_weather(city: str) -> str:
    # mock 数据：Stage 3 将替换为真实搜索 API
    return json.dumps({"city": city, "weather": "晴", "temp_c": 26}, ensure_ascii=False)


def calculator(expression: str) -> str:
    allowed = set("0123456789+-*/(). ")
    if not set(expression) <= allowed:
        return "错误：表达式包含不支持的字符。请只使用数字和 + - * / ( ) 重新构造表达式。"
    try:
        return json.dumps({"expression": expression, "result": eval(expression)})
    except Exception:
        return "错误：表达式无法计算。请检查括号与运算符是否配对，然后重试。"


# 工具注册表：name -> 函数。dispatch 是 agent 唯一的"手"
REGISTRY = {"get_weather": get_weather, "calculator": calculator}


# ============================================================
# Stage 3：真实工具 —— 让 agent 摸到真实世界
#
# ACI 设计要点（对照 Anthropic《Writing Effective Tools》）:
# 1. 返回"高信号"字段（标题/链接/摘要），不返回 uuid 之类的噪音
# 2. 结果必须截断（MAX_TOOL_RESULT_CHARS），否则一次抓取就撑爆上下文
# 3. 错误信息"可行动"：告诉模型下一步该干什么，而不是甩 traceback
# ============================================================

import config

REAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网，返回前几条结果的标题、链接和摘要。"
                           "用于查找事实、新闻、产品信息等。"
                           "如果摘要已足够回答问题，不必再打开链接。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，尽量具体"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "打开一个网页并提取正文文字。仅当搜索摘要不够、"
                           "需要网页里的细节时才使用；不要重复打开同一个链接。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整的网页地址，以 http(s):// 开头"},
                },
                "required": ["url"],
            },
        },
    },
]


def web_search(query: str) -> str:
    import time as _t
    results = None
    err = ""
    # 重试+退避：并行子agent会同时打搜索接口，触发限流是常态，必须重试
    for attempt in range(3):
        try:
            from ddgs import DDGS
            results = list(DDGS().text(query, max_results=5))
            if results:
                break
            err = "无结果"
        except Exception as e:
            err = str(e)
        _t.sleep(2 * (attempt + 1))  # 退避：2s → 4s
    if not results:
        return (f"错误：搜索失败({err})，已重试3次。请稍等片刻后用更简短的关键词重试，"
                f"或先用 fetch_url 直接访问已知的官方网址。")
    trimmed = [
        {"title": r.get("title", "")[:100],
         "url": r.get("href", ""),
         "snippet": (r.get("body") or "")[:300]}
        for r in results
    ]
    return json.dumps(trimmed, ensure_ascii=False)[:config.MAX_TOOL_RESULT_CHARS]


def fetch_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "错误：url 必须以 http:// 或 https:// 开头。请从搜索结果的 url 字段复制完整链接。"
    try:
        import httpx, trafilatura
        resp = httpx.get(url, timeout=15, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (research-agent-tutorial)"})
        resp.raise_for_status()
    except Exception as e:
        return f"错误：网页打开失败({e})。请换一条搜索结果里的其他链接，或仅凭摘要回答。"
    text = trafilatura.extract(resp.text) or ""
    if not text:
        return "错误：该网页没有可提取的正文(可能是纯视频/需要登录)。请换其他链接。"
    text = text[:config.MAX_TOOL_RESULT_CHARS]
    return json.dumps({"url": url, "content": text,
                       "note": f"正文已截断至{len(text)}字符，如需更多细节请告诉我具体要找什么"},
                      ensure_ascii=False)


def fetch_js(url: str, wait_ms: int = 4000) -> str:
    """JS 渲染版抓取：用无头浏览器执行页面脚本后再提取文本。
    分工：静态页用 fetch_url（快）；JS 动态渲染页/静态抓取为空时用本工具（慢但全）。"""
    if not url.startswith(("http://", "https://")):
        return "错误：url 必须以 http:// 或 https:// 开头。请从搜索结果的 url 字段复制完整链接。"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ("错误：未安装 playwright。请运行 "
                "pip install playwright && playwright install chromium 后重试，"
                "或改用 fetch_url。")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # 注意：用默认浏览器UA。实验证明怪异UA会让部分网站的JS走不同渲染分支
            # （yourtools.xyz 实测：默认UA渲染出定价区，自定义UA则整个区域不出现）
            page = browser.new_page()
            page.goto(url, timeout=30000, wait_until="load")
            page.wait_for_timeout(int(wait_ms))   # 给 JS 渲染留时间
            # 滚动到底部触发懒加载（定价/页脚等内容常在滚动后才渲染），再回顶部
            for _ in range(3):
                page.mouse.wheel(0, 20000)
                page.wait_for_timeout(800)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            title = page.title()
            text = page.inner_text("body")        # inner_text 包含页脚（fetch_url 会丢）
            browser.close()
    except Exception as e:
        return (f"错误：JS渲染抓取失败({type(e).__name__})。"
                f"请改用 fetch_url，或基于已有信息作答。")
    text = __import__("re").sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not text:
        return "错误：渲染后页面仍无文本内容。请换其他来源。"
    return json.dumps({"url": url, "title": title, "content": text[:config.MAX_TOOL_RESULT_CHARS],
                       "note": "JS渲染后提取（含页脚），已截断"}, ensure_ascii=False
                      )[:config.MAX_TOOL_RESULT_CHARS]


REGISTRY.update({"web_search": web_search, "fetch_url": fetch_url})

REAL_REGISTRY = {"web_search": web_search, "fetch_url": fetch_url,
                "fetch_js": fetch_js}
# ============================================================
# Stage 4：结构化笔记工具（agentic memory）
#
# 思想（Anthropic《Effective Context Engineering》）：
# 上下文是稀缺资源，而文件系统是廉价的"外置记忆"。
# agent 把关键发现写入 NOTES.md（在上下文之外持久化），
# 需要时再读回——相当于"把工作台上的草稿纸收进抽屉，要用再拿出来"。
# ============================================================

NOTES_FILE = None  # 由使用方（各阶段脚本）初始化为具体路径（全局默认）

# Stage 7：多智能体并行时，每个子agent需要独立的笔记文件。
# 用 thread-local 存储各线程的路径：并行子agent互不干扰，NOTES_FILE 作全局兜底。
import threading as _threading
_notes_tls = _threading.local()


def set_notes_file(path):
    """在【当前线程】内设置笔记文件路径（每个子agent启动时各自调用）。"""
    _notes_tls.path = str(path)


def _current_notes_file():
    return getattr(_notes_tls, "path", None) or NOTES_FILE


def note_write(content: str) -> str:
    import time as _t
    notes = _current_notes_file()
    if not notes:
        return "错误：笔记文件未初始化。请先在代码中设置笔记文件路径。"
    from pathlib import Path as _P
    notes = _P(notes)
    notes.parent.mkdir(parents=True, exist_ok=True)
    with open(notes, "a", encoding="utf-8") as f:
        f.write(f"\n## {_t.strftime('%H:%M:%S')}\n{content[:2000]}\n")
    return "已写入笔记。记住：重要发现务必及时写笔记，上下文可能随时被压缩。"


def note_read(dummy: str = "") -> str:
    from pathlib import Path as _P
    notes = _current_notes_file()
    if not notes or not _P(notes).exists():
        return "笔记为空。请先用 note_write 记录关键发现。"
    text = _P(notes).read_text(encoding="utf-8")
    return text[:config.MAX_TOOL_RESULT_CHARS]


NOTE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "note_write",
            "description": "把重要的研究发现写入持久笔记（上下文会被压缩，"
                           "笔记不会）。每完成一个子问题、或拿到关键数据时立即调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string",
                                "description": "要点式记录：发现+来源域名+数据，一次一条"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "note_read",
            "description": "读取此前的全部研究笔记。在撰写最终报告前调用，"
                           "以笔记为准作答。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

REAL_REGISTRY.update({"note_write": note_write, "note_read": note_read})

# ★把笔记工具加进 tools 说明书——漏了这步，模型根本不知道笔记工具存在
#（Stage 4 实测教训：注册表里有 ≠ 模型知道，模型只认 tools 参数里的清单）
FETCH_JS_TOOL = [{
    "type": "function",
    "function": {
        "name": "fetch_js",
        "description": "渲染页面JavaScript后提取全部可见文本（含页脚定价等）。"
                       "当 fetch_url 返回的内容明显过少、或怀疑信息由JS动态渲染时使用。"
                       "比 fetch_url 慢数秒，不要作为首选。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整的网页地址，以 http(s):// 开头"},
                "wait_ms": {"type": "integer",
                            "description": "渲染等待毫秒数，默认4000。页面很慢时可加大到8000"},
            },
            "required": ["url"],
        },
    },
}]
# 注意：FETCH_JS_TOOL 是列表，必须用 extend（append 会嵌套成 tools[2]=[...] → API 400）
REAL_TOOLS.extend(FETCH_JS_TOOL)

REAL_TOOLS.extend(NOTE_TOOLS)


def dispatch(name: str, args: dict) -> str:
    fn = REGISTRY.get(name)
    if fn is None:
        return f"错误：工具 {name} 不存在。可用工具：{list(REGISTRY)}"
    return fn(**args)

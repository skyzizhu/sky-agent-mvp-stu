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
    try:
        from ddgs import DDGS
        results = list(DDGS().text(query, max_results=5))
    except Exception as e:
        # 可行动的错误：告诉模型重试或换个关键词
        return f"错误：搜索失败({e})。请稍后用更简短的关键词重试。"
    if not results:
        return "没有找到相关结果。请尝试更换关键词：减少词数、或换用同义表达再搜一次。"
    # 高信号输出：只要 title/url/description，每条摘要也截断
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


REGISTRY.update({"web_search": web_search, "fetch_url": fetch_url})

REAL_REGISTRY = {"web_search": web_search, "fetch_url": fetch_url}
# ============================================================
# Stage 4：结构化笔记工具（agentic memory）
#
# 思想（Anthropic《Effective Context Engineering》）：
# 上下文是稀缺资源，而文件系统是廉价的"外置记忆"。
# agent 把关键发现写入 NOTES.md（在上下文之外持久化），
# 需要时再读回——相当于"把工作台上的草稿纸收进抽屉，要用再拿出来"。
# ============================================================

NOTES_FILE = None  # 由使用方（各阶段脚本）初始化为具体路径


def note_write(content: str) -> str:
    import time as _t
    if NOTES_FILE is None:
        return "错误：笔记文件未初始化。请先在代码中设置 tools.NOTES_FILE。"
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n## {_t.strftime('%H:%M:%S')}\n{content[:2000]}\n")
    return "已写入笔记。记住：重要发现务必及时写笔记，上下文可能随时被压缩。"


def note_read(dummy: str = "") -> str:
    if NOTES_FILE is None or not NOTES_FILE.exists():
        return "笔记为空。请先用 note_write 记录关键发现。"
    text = NOTES_FILE.read_text(encoding="utf-8")
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
REAL_TOOLS.extend(NOTE_TOOLS)


def dispatch(name: str, args: dict) -> str:
    fn = REGISTRY.get(name)
    if fn is None:
        return f"错误：工具 {name} 不存在。可用工具：{list(REGISTRY)}"
    return fn(**args)

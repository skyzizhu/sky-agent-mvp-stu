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
                           "【摘要优先原则】：如果搜索摘要已足够证实事实、数字或定义，应直接 note_write 记录并保留对应 url 引用，无需盲目打开每一个链接；仅当摘要缺少关键细节时才调用 fetch_url。",
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
            "description": "打开一个网页并提取正文文字。系统内置 SPA 前端动态渲染探测与自动降级无头浏览器能力。"
                           "仅当搜索摘要不够、需要网页深层细节时才使用；不要重复打开同一个链接；若某域名多次抓取失败，建议基于摘要回答或更换其他来源。",
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


def smart_truncate(text: str, limit: int, tail_ratio: float = 0.3) -> str:
    """头尾保留截断：定价表/页脚/联系方式等高价值信息常在页面中后部，
    纯头部截断会切掉。保留前 (1-tail_ratio) 与尾部，中间以标记衔接。"""
    if len(text) <= limit:
        return text
    marker = "\n……[中间内容已截断]……\n"
    head = int(limit * (1 - tail_ratio)) - len(marker)
    tail = limit - head - len(marker)
    if tail <= 0:
        return text[:limit]
    return text[:head] + marker + text[-tail:]


def _tavily_search(query: str) -> list | None:
    """Tavily 搜索（付费，质量高速度快）。未配 key 返回 None 走降级。"""
    key = config.TAVILY_API_KEY
    if not key:
        return None
    import httpx as _httpx
    resp = _httpx.post("https://api.tavily.com/search",
                       json={"api_key": key, "query": query,
                             "max_results": 5, "search_depth": "basic"},
                       timeout=20)
    resp.raise_for_status()
    out = []
    for r in resp.json().get("results", []):
        out.append({"title": r.get("title", ""), "href": r.get("url", ""),
                    "body": r.get("content", "")})
    return out


def web_search(query: str) -> str:
    import time as _t
    results = None
    err = ""
    # 优先 Tavily（配置了 key 时）；失败/未配置降级免费 ddgs
    try:
        tav = _tavily_search(query)
        if tav is not None:
            results = tav
    except Exception as e:
        err = f"tavily: {e}"
    # 免费降级通道：重试+退避（并行 worker 同时打接口触发限流是常态）
    if not results:
        for attempt in range(3):
            try:
                from ddgs import DDGS
                results = list(DDGS().text(query, max_results=5))
                if results:
                    break
                err = err or "无结果"
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
    dump = json.dumps(trimmed, ensure_ascii=False)
    while len(dump) > config.MAX_TOOL_RESULT_CHARS and len(trimmed) > 1:
        trimmed.pop()   # 丢末尾条目保 JSON 结构合法，而不是拦腰切断
        dump = json.dumps(trimmed, ensure_ascii=False)
    return dump


SPA_KEYWORDS = (
    "enable javascript", "需要启用 javascript", "请开启 javascript",
    "正在加载", "loading...", "javascript is required", "请开启 js",
    "redirecting...", "页面加载中"
)


def _is_spa_or_empty(text: str) -> bool:
    if not text:
        return True
    t = text.strip().lower()
    if len(t) < 150:
        return True
    if any(k in t for k in SPA_KEYWORDS) and len(t) < 400:
        return True
    return False


def fetch_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "错误：url 必须以 http:// 或 https:// 开头。请从搜索结果的 url 字段复制完整链接。"
    
    httpx_err = None
    text = ""
    try:
        import httpx, trafilatura
        resp = httpx.get(
            url, timeout=15, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        )
        resp.raise_for_status()
        text = trafilatura.extract(resp.text) or ""
    except Exception as e:
        httpx_err = e

    # 1. 静态抓取成功且正文充分，直接返回
    if not httpx_err and not _is_spa_or_empty(text):
        text = smart_truncate(text, config.MAX_TOOL_RESULT_CHARS)   # 头尾保留：页脚定价不再被切
        return json.dumps({"url": url, "content": text,
                           "note": f"正文头尾保留截断至{len(text)}字符，中间缺失如需请告知"},
                          ensure_ascii=False)

    # 2. 静态抓取为空/短小(<150字)/SPA骨架屏或网络受阻时，尝试自动降级调用无头浏览器 JS 渲染
    try:
        js_res = fetch_js(url, wait_ms=3000)
        if isinstance(js_res, str) and not js_res.startswith("错误："):
            js_data = json.loads(js_res)
            if js_data.get("content") and len(js_data["content"].strip()) >= 100:
                js_data["auto_fallback"] = True
                js_data["note"] = f"静态抓取无有效正文/SPA骨架屏，已自动降级为无头浏览器JS渲染获取({len(js_data['content'])}字)"
                return json.dumps(js_data, ensure_ascii=False)
    except Exception:
        pass

    # 3. 两种方式均未提取到有效正文，返回结构化错误与引导建议
    reason = f"({httpx_err})" if httpx_err else "(静态提取与JS渲染均无有效正文/可能是纯视频/需登录/强反爬验证)"
    return (
        f"错误：网页抓取失败{reason}。建议不要继续抓取该链接，"
        f"优先依据搜索结果里的摘要(snippet)回答，或更换其他搜索结果链接。"
    )


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
    body = smart_truncate(text, config.MAX_TOOL_RESULT_CHARS)
    return json.dumps({"url": url, "title": title, "content": body,
                       "note": "JS渲染后提取（含页脚），头尾保留截断"}, ensure_ascii=False)


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
    return smart_truncate(text, config.MAX_TOOL_RESULT_CHARS)


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
                       "两个使用时机：① fetch_url 返回的内容明显过少时，优先用本工具重新抓取【同一个网址】"
                       "（很多页面正文由JS动态渲染，静态抓取只有壳）；② 怀疑信息由JS动态渲染时。"
                       "比 fetch_url 慢数秒。",
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


def update_checklist(sub_id: int, status: str, summary: str = "") -> str:
    """更新大纲子问题查证进度（默认回执，运行时由 agent_core 的看板状态机接管）。"""
    return f"子问题 {sub_id} 状态已更新为 {status}" + (f"（结论：{summary}）" if summary else "")


CHECKLIST_TOOL = [{
    "type": "function",
    "function": {
        "name": "update_checklist",
        "description": "更新研究大纲子问题的查证进度看板。每当查证完一个子问题并记下笔记后调用，"
                       "将对应子问题状态改为 completed 并填写简要结论。"
                       "所有子问题 completed 后，请立即 note_read 结题出报告。",
        "parameters": {
            "type": "object",
            "properties": {
                "sub_id": {"type": "integer", "description": "要更新的子问题编号（如 1, 2, 3）"},
                "status": {"type": "string", "enum": ["in_progress", "completed"],
                           "description": "子问题状态：in_progress(正在查证) 或 completed(已查证完备)"},
                "summary": {"type": "string",
                            "description": "核心事实结论（不超过50字，status为completed时必填）"},
            },
            "required": ["sub_id", "status"],
        },
    },
}]

REAL_TOOLS.extend(CHECKLIST_TOOL)
REAL_REGISTRY.update({"update_checklist": update_checklist})

# 预先将 REAL_REGISTRY 合并进 REGISTRY，确保未显式传 registry 的调用方也能找到完整工具
REGISTRY.update(REAL_REGISTRY)


def _append_advisory(result: str, advisory: str | None) -> str:
    if not advisory:
        return result
    try:
        data = json.loads(result)
        if isinstance(data, dict):
            data["advisory"] = advisory.strip()
            if "note" in data:
                data["note"] = f"{data['note']} | {advisory.strip()}"
            else:
                data["note"] = advisory.strip()
            return json.dumps(data, ensure_ascii=False)
    except Exception:
        pass
    return result + advisory


def dispatch(name: str, args: dict, registry: dict | None = None,
             cache=None, step: int = 0,
             extract_client=None, goal: str = "",
             on_extract_usage=None) -> str:
    reg = registry if registry is not None else REGISTRY
    fn = reg.get(name)
    if fn is None:
        return f"错误：工具 {name} 不存在。可用工具：{list(reg)}"

    # 缓存与去重拦截检查
    if cache is not None:
        if name == "web_search":
            query = args.get("query", "")
            hit, cached_res, advisory = cache.check_search(query, step=step)
            if hit:
                return _append_advisory(cached_res, advisory)
        elif name in ("fetch_url", "fetch_js"):
            url = args.get("url", "")
            hit, cached_res, advisory = cache.check_url(url)
            if hit:
                return _append_advisory(cached_res, advisory)

    result = fn(**args)

    # 单页即时萃取（Map 阶段）：长网页在喂入上下文前由轻量 LLM 提纯核心事实与数据
    if (name in ("fetch_url", "fetch_js")
            and extract_client is not None
            and isinstance(result, str)
            and not result.startswith("错误")):
        try:
            from common.context import extract_page_facts, PAGE_EXTRACT_THRESHOLD
            data = None
            try:
                data = json.loads(result)
            except Exception:
                pass

            if isinstance(data, dict) and "content" in data:
                raw_text = data["content"]
                if len(raw_text) > PAGE_EXTRACT_THRESHOLD:
                    extracted, usage = extract_page_facts(extract_client, raw_text, goal=goal)
                    if usage and on_extract_usage:
                        on_extract_usage(usage)
                    if extracted and len(extracted) < len(raw_text):
                        ratio = round((1 - len(extracted) / len(raw_text)) * 100)
                        data["content"] = extracted
                        data["note"] = (f"已通过单页Map即时萃取提纯（原长 {len(raw_text)} 字 → "
                                        f"精炼至 {len(extracted)} 字，压缩比 {ratio}%）")
                        result = json.dumps(data, ensure_ascii=False)
                        if cache is not None:
                            cache.last_extracted = True
                            cache.last_raw_chars = len(raw_text)
                            cache.last_extracted_chars = len(extracted)
                            cache.last_extract_ratio = ratio
        except Exception:
            pass  # 萃取异常时不阻断，降级保留原长文

    # 状态收集：是否触发了无缝自动降级 JS 渲染
    if cache is not None and isinstance(result, str):
        if '"auto_fallback": true' in result or '"auto_fallback": True' in result:
            cache.last_auto_fallback = True

    # 域名失败感知：若抓取失败，累计该域名失败次数并附带引导建议
    if cache is not None and isinstance(result, str) and result.startswith("错误") and name in ("fetch_url", "fetch_js"):
        url = args.get("url", "")
        cache.record_domain_failure(url)
        domain_advisory = cache.check_domain_advisory(url)
        if domain_advisory:
            result = result + domain_advisory

    # 成功执行后回填缓存（错误/反爬拦截不缓存，保留重试机会）
    if cache is not None and isinstance(result, str) and not result.startswith("错误"):
        if name == "web_search":
            cache.set_search(args.get("query", ""), result, step=step)
        elif name in ("fetch_url", "fetch_js"):
            cache.set_url(args.get("url", ""), result)

    return result


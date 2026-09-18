"""
common/cache.py - 会话级工具去重与缓存管理器（SessionToolCache）

核心目标：
1. 搜索精确匹配与近义词伪改写去重：
   - 相同或高度雷同 Query（重合度 > 75%）直接复用缓存，0ms 响应，0 外部 API 消耗；
   - 在返回结果末尾附加主动引导语（Advisory），打破 Agent 重复搜索的死循环。
2. URL 网页内容防重拉取：
   - 抓取过的 URL 直接复用解析后正文，防止重复下载和反爬 403 踩坑，保护上下文窗口。
3. 零外部重型依赖（纯 Python 原生算法，无需 torch/embedding）。
"""

import re
import time
from typing import Tuple
from urllib.parse import urlparse


def extract_domain(url: str) -> str:
    """提取 URL 的主域名（去协议和路径）。"""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        netloc = parsed.netloc or parsed.path.split("/")[0]
        return netloc.lower().split(":")[0]
    except Exception:
        return ""


STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "怎么", "与", "及", "等", "如何",
    "什么", "哪些", "最新", "详细", "介绍", "分析", "模式", "方式"
}


def normalize_query(query: str) -> str:
    """归一化搜索 Query：转小写、清除多余空白与标点符号。"""
    if not query:
        return ""
    q = query.lower().strip()
    # 替换各种符号为空格
    q = re.sub(r"[^\w\u4e00-\u9fa5]+", " ", q)
    return " ".join(q.split())


def clean_chars(query: str) -> list[str]:
    """清洗查询串：转小写、清除标点与空白，过滤单字停用词。"""
    if not query:
        return []
    q = query.lower().strip()
    q = re.sub(r"[^\w\u4e00-\u9fa5]+", "", q)
    return [c for c in q if c not in STOP_WORDS]


def extract_features(query: str) -> set:
    """提取 Query 的特征集合（清洗后的单字 + 连续 2-gram）。"""
    chars = clean_chars(query)
    if not chars:
        return set()

    features = set(chars)
    for i in range(len(chars) - 1):
        features.add(chars[i] + chars[i + 1])
    return features


def query_similarity(q1: str, q2: str) -> float:
    """计算两个 Query 的语义/词形重合度（结合 Jaccard 与 Overlap 系数）。"""
    norm1, norm2 = normalize_query(q1), normalize_query(q2)
    if norm1 == norm2:
        return 1.0

    f1 = extract_features(q1)
    f2 = extract_features(q2)

    if not f1 or not f2:
        return 0.0

    inter = len(f1 & f2)
    union = len(f1 | f2)
    jaccard = inter / union if union > 0 else 0.0
    overlap = inter / min(len(f1), len(f2)) if min(len(f1), len(f2)) > 0 else 0.0

    # 综合得分：加权平均（对子集包含与高重叠更敏感）
    return 0.4 * jaccard + 0.6 * overlap


class SessionToolCache:
    """单个 Agent 运行会话的工具调用缓存与去重治理器。"""

    def __init__(self, fuzzy_threshold: float = 0.75):
        self.fuzzy_threshold = fuzzy_threshold
        # search_cache: normalized_query -> {"raw_query": str, "result": str, "time": float, "step": int}
        self.search_cache = {}
        # url_cache: normalized_url -> {"result": str, "time": float}
        self.url_cache = {}
        # 观测状态记录（供当前 step 检查与事件回传）
        self.last_hit = False
        self.last_hit_type = None  # "search_exact" | "search_fuzzy" | "url_exact"
        self.last_advisory = None
        self.total_saved_calls = 0

        # 单页萃取状态追踪
        self.last_extracted = False
        self.last_raw_chars = 0
        self.last_extracted_chars = 0
        self.last_extract_ratio = 0

        # 域名失败感知与无缝降级追踪
        self.domain_failures = {}  # domain -> int (失败次数)
        self.last_auto_fallback = False

    def reset_step_state(self):
        """每轮调用前复位瞬时状态。"""
        self.last_hit = False
        self.last_hit_type = None
        self.last_advisory = None
        self.last_extracted = False
        self.last_raw_chars = 0
        self.last_extracted_chars = 0
        self.last_extract_ratio = 0
        self.last_auto_fallback = False

    # ---------- 搜索去重与缓存 ----------

    def check_search(self, query: str, step: int = 0) -> Tuple[bool, str | None, str | None]:
        """
        检查 query 是否命中缓存或相似去重。
        返回：(hit: bool, cached_result: str | None, advisory: str | None)
        """
        self.reset_step_state()
        norm = normalize_query(query)
        if not norm:
            return False, None, None

        # 1. 精确匹配检查
        if norm in self.search_cache:
            entry = self.search_cache[norm]
            prev_step = entry.get("step", 0)
            step_info = f"在第 {prev_step} 步" if prev_step > 0 else "此前"
            advisory = (
                f"\n\n⚠️【系统提示：重复检索】该关键词（'{entry['raw_query']}'）已{step_info}检索过，"
                f"上文为已缓存的真实结果。请勿反复检索同一方向，建议对照大纲切换其他未完成子问题，"
                f"或提取更具体的实体词/细分场景深入挖掘。"
            )
            self.last_hit = True
            self.last_hit_type = "search_exact"
            self.last_advisory = advisory
            self.total_saved_calls += 1
            return True, entry["result"], advisory

        # 2. 模糊重合度检查（近义词/伪改写）
        best_match = None
        best_score = 0.0
        for cached_norm, entry in self.search_cache.items():
            score = query_similarity(query, entry["raw_query"])
            if score > best_score:
                best_score = score
                best_match = entry

        if best_match and best_score >= self.fuzzy_threshold:
            prev_step = best_match.get("step", 0)
            step_info = f"在第 {prev_step} 步" if prev_step > 0 else "此前"
            advisory = (
                f"\n\n💡【系统提示：相似检索复用】检测到该查询与{step_info}检索的 '{best_match['raw_query']}' "
                f"高度重合（重合度 {int(best_score * 100)}%），系统已直接复用该结果。"
                f"建议：如需获取增量信息，请更换不同维度的关键词（如具体功能、企业版定价或权限机制）。"
            )
            self.last_hit = True
            self.last_hit_type = "search_fuzzy"
            self.last_advisory = advisory
            self.total_saved_calls += 1
            return True, best_match["result"], advisory

        return False, None, None

    def set_search(self, query: str, result: str, step: int = 0):
        """将成功获取的搜索结果存入缓存。"""
        norm = normalize_query(query)
        if norm and result and not result.startswith("错误："):
            self.search_cache[norm] = {
                "raw_query": query,
                "result": result,
                "time": time.time(),
                "step": step,
            }

    # ---------- 网页抓取防重与缓存 ----------

    def check_url(self, url: str) -> Tuple[bool, str | None, str | None]:
        """检查 URL 是否已抓取过。"""
        self.reset_step_state()
        clean_url = url.strip().rstrip("/")
        if clean_url in self.url_cache:
            entry = self.url_cache[clean_url]
            advisory = (
                f"\n\n⚡【系统提示：网页防重拉取】该 URL 此前已抓取并解析过，系统已直接提取内容副本（0ms 响应）。"
                f"请基于已有内容提炼笔记，无需重复打开同一链接。"
            )
            self.last_hit = True
            self.last_hit_type = "url_exact"
            self.last_advisory = advisory
            self.total_saved_calls += 1
            return True, entry["result"], advisory
        return False, None, None

    def set_url(self, url: str, content: str):
        """将成功抓取的网页内容存入缓存。"""
        clean_url = url.strip().rstrip("/")
        if clean_url and content and not content.startswith("错误："):
            self.url_cache[clean_url] = {
                "result": content,
                "time": time.time(),
            }

    # ---------- 域名级失败感知与引导 ----------

    def record_domain_failure(self, url: str) -> int:
        """记录该 URL 所在域名的失败/反爬拦截次数。"""
        domain = extract_domain(url)
        if domain:
            self.domain_failures[domain] = self.domain_failures.get(domain, 0) + 1
            return self.domain_failures[domain]
        return 0

    def get_domain_failures(self, url: str) -> int:
        """查询该 URL 对应域名的历史失败次数。"""
        domain = extract_domain(url)
        return self.domain_failures.get(domain, 0) if domain else 0

    def check_domain_advisory(self, url: str) -> str | None:
        """若该域名累计失败 >= 2 次，生成阻断/引导建议。"""
        failures = self.get_domain_failures(url)
        domain = extract_domain(url)
        if failures >= 2 and domain:
            return (
                f"\n\n💡【系统提示：该域名已多次抓取失败】检测到域名 '{domain}' 近期已有 {failures} 次抓取失败或反爬拦截。"
                f"建议不要重复尝试该域名下的其他链接，优先依据搜索返回的摘要信息或切换其他第三方站点。"
            )
        return None


"""
Stage 8 节点17：跨会话记忆（Memory）。

和 Stage 4 笔记的区别：笔记=任务内的研究发现（用完即弃）；
记忆=关于【用户本人】的稳定偏好（跨会话长期有效）。

三条铁律：
1. 只记"用户偏好/事实"，绝不记调研内容（调研结论属于笔记，混进来会污染）
2. 注入时机 = 新会话启动时（拼进 system prompt 开头，位置稳定利于缓存）
3. 容量有上限（20条），重复的合并——记忆不管理会膨胀成噪音
"""
import json
import re
import time
from pathlib import Path
from typing import Optional, List, Tuple, Dict

GLOBAL_PREF_KEYWORDS = (
    "输出语言", "语言习惯", "输出格式", "格式偏好", "输出规范", "研究输出规范",
    "称呼", "回答风格", "表格对比", "引用格式", "链接规范"
)

DOMAIN_STOPWORDS = {
    "研究领域", "关注方向", "身份与关注", "竞品调研", "以个人开发者身份",
    "关于", "等等", "相关", "动态", "产品", "方向", "身份", "领域", "习惯", "偏好",
    "工作", "模式", "流程", "要求", "进行", "使用", "方案", "问题", "如何", "什么"
}

PRICING_KEYWORDS = {
    "定价", "价格", "收费", "计费", "多少钱", "费用", "会员", "订阅",
    "比价", "涨价", "降价", "付费", "档位", "套餐", "成本", "便宜", "贵"
}

COMPLIANCE_KEYWORDS = {
    "合规", "认证", "资质", "备案", "政策", "审核", "规范", "牌照"
}


def is_global_preference(text: str) -> bool:
    """判断一条偏好是否属于跨所有研究任务都生效的通用规范（如语言、表格、引用等）。"""
    t = text.strip()
    for kw in GLOBAL_PREF_KEYWORDS:
        if t.startswith(kw) or f"【{kw}】" in t or f"{kw}：" in t or f"{kw}:" in t:
            return True
    if any(k in t for k in ("输出语言", "输出格式", "格式偏好", "表格对比", "保留来源", "禁止编造", "引用编号")):
        if not any(k in t for k in ("研究领域", "关注方向", "业务方向")):
            return True
    return False


def is_domain_preference_relevant(pref_text: str, question: str) -> bool:
    """
    判断领域偏好是否与用户当前课题高度相关。
    核心原则：避免'仅因提到某公司名字，就把无关的定价比价记忆强加给架构/工作模式问题'的负迁移。
    """
    if not question:
        return True
    
    q_lower = question.lower()
    pref_lower = pref_text.lower()

    # 1. 如果偏好是关于'定价/订阅/计费/价格'
    is_pricing_pref = any(k in pref_lower for k in PRICING_KEYWORDS)
    if is_pricing_pref:
        q_has_pricing = any(k in q_lower for k in PRICING_KEYWORDS)
        if not q_has_pricing:
            # 用户当前问题完全没问价格/订阅（例如只问'工作模式'或'技术原理'），过滤
            return False

    # 2. 如果偏好是关于'合规/认证/资质'
    is_compliance_pref = any(k in pref_lower for k in COMPLIANCE_KEYWORDS)
    if is_compliance_pref:
        q_has_compliance = any(k in q_lower for k in COMPLIANCE_KEYWORDS)
        if not q_has_compliance and not any(k in q_lower for k in ("微信", "小程序", "开发")):
            return False

    # 3. 实体/英文词与中文 2-gram 交集匹配
    clean = re.sub(r"^[^：:]+[：:]", "", pref_lower)
    en_words = set(re.findall(r"[a-z0-9_]{2,}", clean))
    q_en_words = set(re.findall(r"[a-z0-9_]{2,}", q_lower))
    if en_words and (en_words & q_en_words):
        return True

    for sw in DOMAIN_STOPWORDS:
        clean = clean.replace(sw, "")
    zh_chars = "".join(re.findall(r"[\u4e00-\u9fff]", clean))
    pref_zh_grams = {zh_chars[i:i+2] for i in range(len(zh_chars) - 1)}

    q_clean = q_lower
    for sw in DOMAIN_STOPWORDS:
        q_clean = q_clean.replace(sw, "")
    q_zh_chars = "".join(re.findall(r"[\u4e00-\u9fff]", q_clean))
    q_zh_grams = {q_zh_chars[i:i+2] for i in range(len(q_zh_chars) - 1)}

    return len(pref_zh_grams & q_zh_grams) > 0


class MemoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"facts": []}  # 每条: {"text": 偏好描述, "ts": 时间}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def format_for_prompt(self, question: Optional[str] = None) -> str:
        """注入 system prompt 的文本形态。空记忆返回空串（不占 token）。
        当提供 question 时，执行相关性门禁过滤（通用偏好保留，无关领域偏好排除）。"""
        injected, _ = self.get_relevant_facts(question)
        if not injected:
            return ""
        lines = [f"- {text}" for text in injected]
        if question:
            header = "【用户长期记忆（根据当前问题匹配的相关偏好，请遵守）】\n"
        else:
            header = "【用户长期记忆（历史会话中沉淀的偏好，请遵守）】\n"
        return header + "\n".join(lines)

    def get_relevant_facts(self, question: Optional[str] = None) -> Tuple[List[str], List[str]]:
        """
        根据当前课题筛选偏好：返回 (注入偏好列表, 过滤偏好列表)。
        - 通用规范偏好（输出语言、表格对比、引用规范等）：100% 稳定注入。
        - 领域特定偏好（会员定价比价、小程序开发认证等）：仅当与当前问题相关时召回，避免带偏调研。
        - 若 question 为空，则全量放行（向后兼容）。
        """
        facts = self.data.get("facts", [])
        if not facts:
            return [], []
        
        all_texts = [f["text"] for f in facts[-20:]]
        if not question:
            return all_texts, []

        injected, filtered = [], []
        for text in all_texts:
            if is_global_preference(text):
                injected.append(text)
            elif is_domain_preference_relevant(text, question):
                injected.append(text)
            else:
                filtered.append(text)
        return injected, filtered

    def get_stats_for_prompt(self, question: Optional[str] = None) -> Dict[str, int]:
        """获取记忆注入统计指标（总数、注入数、过滤数）。"""
        injected, filtered = self.get_relevant_facts(question)
        return {
            "total": len(injected) + len(filtered),
            "injected": len(injected),
            "filtered": len(filtered),
        }

    def merge(self, new_preferences: list, client=None) -> int:
        """合并新偏好。有 client 时走 LLM 语义合并（同义合并/冲突以新为准/格式统一），
        失败降级为子串判重规则。"""
        new_preferences = [p.strip() for p in new_preferences
                           if isinstance(p, str) and len(p.strip()) >= 4]
        if not new_preferences:
            return 0
        if client is not None:
            try:
                return self._merge_semantic(client, new_preferences)
            except Exception:
                pass  # 任何失败都降级为规则合并
        return self._merge_rule(new_preferences)

    def _merge_semantic(self, client, new_preferences: list) -> int:
        from common.llm_client import call_llm
        existing = [f["text"] for f in self.data["facts"]]
        msg, _ = call_llm(
            client,
            [{"role": "system",
              "content": '你是记忆管理员。把【新偏好】合并进【现有记忆】。规则：'
                         '同义/近义的合并为一条（保留表述更完整的）；相互冲突的以新偏好为准改写；'
                         '措辞统一（不要同一条出现两种写法）。只输出 JSON：'
                         '{"facts": ["合并后的完整记忆列表"]}，总数不超过 20 条。'},
             {"role": "user",
              "content": f"【现有记忆】\n{json.dumps(existing, ensure_ascii=False)}\n\n"
                         f"【新偏好】\n{json.dumps(new_preferences, ensure_ascii=False)}"}],
            response_format={"type": "json_object"},
        )
        merged = [t.strip() for t in json.loads(msg.content).get("facts", [])
                  if isinstance(t, str) and len(t.strip()) >= 4][:20]
        # 保留原时间戳：旧条目沿用旧 ts，新增条目用今天
        ts_map = {f["text"]: f["ts"] for f in self.data["facts"]}
        before = set(existing)
        self.data["facts"] = [{"text": t,
                               "ts": ts_map.get(t, time.strftime("%Y-%m-%d"))}
                              for t in merged]
        self.save()
        return sum(1 for t in (f["text"] for f in self.data["facts"]) if t not in before)

    def _merge_rule(self, new_preferences: list) -> int:
        """规则降级：子串判重 + 容量上限。"""
        added = 0
        existing = [f["text"] for f in self.data["facts"]]
        for text in new_preferences:
            if any(text in e or e in text for e in existing):
                continue
            self.data["facts"].append({"text": text, "ts": time.strftime("%Y-%m-%d")})
            existing.append(text)
            added += 1
        self.data["facts"] = self.data["facts"][-20:]
        self.save()
        return added


def extract_preferences(client, transcript_text: str) -> list:
    """从一段对话里提取'用户长期偏好'。关键在提示词划清边界：偏好≠调研内容。"""
    from common.llm_client import call_llm
    msg, _ = call_llm(
        client,
        [{"role": "system",
          "content": '从对话记录中提取【用户的长期偏好】——只提取关于用户本人的、'
                     '跨会话依然有效的稳定信息：研究领域/关注方向、输出格式偏好、'
                     '语言习惯、称呼。只输出JSON：{"preferences": ["...", "..."]}。'
                     '严禁把调研内容（某个产品的价格、某个事实）当偏好；'
                     '本次对话专有的临时要求（如"今天发给某人"）也不算；没有就给空数组。'},
         {"role": "user", "content": transcript_text[:20000]}],
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(msg.content).get("preferences", [])
    except json.JSONDecodeError:
        return []

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
import time
from pathlib import Path


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

    def format_for_prompt(self) -> str:
        """注入 system prompt 的文本形态。空记忆返回空串（不占 token）。"""
        facts = self.data.get("facts", [])
        if not facts:
            return ""
        lines = [f"- {f['text']}" for f in facts[-20:]]
        return "【用户长期记忆（历史会话中沉淀的偏好，请遵守）】\n" + "\n".join(lines)

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

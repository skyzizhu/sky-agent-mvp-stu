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

    def merge(self, new_preferences: list) -> int:
        """合并新偏好。MVP用去重规则：与已有条目相同或被包含则跳过。
        （进阶版应让LLM做语义合并与冲突更新，见 notes）"""
        added = 0
        existing = [f["text"] for f in self.data["facts"]]
        for text in new_preferences:
            text = text.strip()
            if not text or len(text) < 4:
                continue
            if any(text in e or e in text for e in existing):
                continue
            self.data["facts"].append({"text": text, "ts": time.strftime("%Y-%m-%d")})
            existing.append(text)
            added += 1
        # 容量上限：超过20条丢最旧的
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

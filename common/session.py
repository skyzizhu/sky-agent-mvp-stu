"""
多轮研究会话管理：把多次运行串成一个有记忆的研究会话。

三档能力：
  第一档：笔记续跑 —— 同 session 共用 notes.md（不每次清空）
  第二档：上下文延续 —— 上轮的摘要/发现注入下轮的 system
  第三档：多轮对话 —— 用户自由追问，agent 理解指代

一个 Session 的生命周期：
  创建 → 多次 run() → 笔记持续追加 → 上下文持续更新 → 用户关闭
"""
import json
import time
import uuid
from pathlib import Path


class Session:
    """一次研究会话：多次运行共享笔记、上下文和运行历史。"""

    def __init__(self, session_id: str, base_dir: Path):
        self.id = session_id
        self.dir = base_dir / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.dir / "meta.json"
        self.notes_path = self.dir / "notes.md"
        self.context_path = self.dir / "context.json"
        self.meta = self._load_meta()

    def _load_meta(self) -> dict:
        if self.meta_path.exists():
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        return {"session_id": self.id, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "runs": [], "topic": ""}

    def save_meta(self):
        self.meta_path.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2),
                                  encoding="utf-8")

    def read_notes(self) -> str:
        """读取本 session 的全部研究笔记（新 session 返回空串）。"""
        if self.notes_path.exists():
            return self.notes_path.read_text(encoding="utf-8")
        return ""

    def append_notes(self, text: str):
        with open(self.notes_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    # ---------- 上下文延续（第二档） ----------
    def save_context(self, summary: str, key_findings: list, pending: list):
        """保存跨运行上下文快照——下轮运行启动时注入。"""
        ctx = {"summary": summary, "key_findings": key_findings,
               "pending": pending, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.context_path.write_text(json.dumps(ctx, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    def load_context(self) -> dict | None:
        if self.context_path.exists():
            return json.loads(self.context_path.read_text(encoding="utf-8"))
        return None

    # ---------- 运行注册 ----------
    def add_run(self, run_id: str, question: str):
        self.meta["runs"].append({"run_id": run_id, "question": question[:80],
                                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        self.save_meta()

    @property
    def run_count(self) -> int:
        return len(self.meta.get("runs", []))

    @property
    def is_new(self) -> bool:
        """是否是新 session（还没有任何运行记录）。"""
        return self.run_count == 0

    # ---------- 序列化 ----------
    def to_dict(self) -> dict:
        return {"session_id": self.id, "meta": self.meta,
                "notes_exists": self.notes_path.exists()}


class SessionStore:
    """管理多个研究会话。"""

    def __init__(self, base_dir: Path):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self._sessions = {}   # session_id -> Session

    def create(self) -> Session:
        sid = uuid.uuid4().hex[:8]
        s = Session(sid, self.base)
        s.save_meta()
        self._sessions[sid] = s
        return s

    def get(self, session_id: str) -> Session | None:
        if session_id in self._sessions:
            return self._sessions[session_id]
        p = self.base / session_id / "meta.json"
        if p.exists():
            s = Session(session_id, self.base)
            self._sessions[session_id] = s
            return s
        return None

    def get_or_create(self, session_id: str) -> Session:
        s = self.get(session_id)
        if s is None:
            s = Session(session_id, self.base)
            s.save_meta()
            self._sessions[session_id] = s
        return s

    def list_sessions(self) -> list:
        out = []
        for d in sorted(self.base.iterdir(), reverse=True):
            if d.is_dir():
                meta_p = d / "meta.json"
                if meta_p.exists():
                    try:
                        meta = json.loads(meta_p.read_text(encoding="utf-8"))
                        out.append({"session_id": d.name,
                                    "created": meta.get("created", ""),
                                    "topic": meta.get("topic", ""),
                                    "run_count": len(meta.get("runs", []))})
                    except json.JSONDecodeError:
                        continue
        return out

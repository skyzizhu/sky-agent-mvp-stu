"""
Stage 9 节点20：统一运行日志（RunLog）。

原则：一处写入（log_run），处处可查（load_runs）。
所有运行（任何阶段的实现）都以统一 schema 追加到 logs/runs.jsonl——
这是失败归类和观测面板的唯一数据源。

统一入口的教训（Stage 6 实测）：绕过统一入口的组件 = 观测盲区。
"""
import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "logs" / "runs.jsonl"

SCHEMA_HINT = {
    "run_id": "本次运行唯一id",
    "ts": "运行时间",
    "impl": "实现标识（如 08_production / 07_multi_agent）",
    "question": "用户问题",
    "steps": "循环步数",
    "tool_calls": "工具调用名序列（按发生顺序）",
    "tokens": "总token消耗",
    "budget_max": "预算上限（无则null）",
    "stop_reason": "model_done / budget_exhausted / max_steps / error",
    "errors": "错误清单（每项一条字符串）",
    "extra": "各实现自定义字段（如 new_preferences）",
}


def log_run(**fields) -> dict:
    rec = {"run_id": uuid.uuid4().hex[:8],
           "ts": time.strftime("%Y-%m-%d %H:%M:%S"), **fields}
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_runs() -> list:
    if not LOG_PATH.exists():
        return []
    runs = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 坏行跳过但不吞掉——观测系统自己也要容错
    return runs

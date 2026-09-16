"""
Stage 9：失败模式归类器。
读 logs/runs.jsonl，把"非正常收尾/带错误"的运行交给 LLM 归到固定类目，
输出失败分布表 → logs/failure_report.md（观测面板会读取它）。

用法: .venv/bin/python stages/09_observability/classify_failures.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.llm_client import make_client, call_llm
from common.observability import load_runs

CATEGORIES = ["工具失败/限流", "未收敛/步数耗尽", "预算截断",
              "数据缺失(工具正常但没查到)", "审批拒绝(HITL)", "无失败"]

def main():
    runs = load_runs()
    if not runs:
        print("logs/runs.jsonl 为空——先跑几次 agent 再来。")
        return

    failures = [r for r in runs
                if r.get("stop_reason") != "model_done" or r.get("errors")]
    print(f"共 {len(runs)} 次运行，其中 {len(failures)} 次需要归类：")

    client = make_client()
    classified = []
    for r in failures:
        brief = (f"问题:{r.get('question','')[:60]} | 停止原因:{r.get('stop_reason')} | "
                 f"步数:{r.get('steps')} | 工具序列:{r.get('tool_calls', [])[:12]} | "
                 f"错误:{(r.get('errors') or ['无'])[:3]}")
        msg, _ = call_llm(client,
            [{"role": "system",
              "content": f"把一次agent运行归入以下类目之一（只输出JSON："
                         f'{{"category":"...","evidence":"一句话依据"}}。'
                         f'类目：{CATEGORIES}。注意"审批拒绝"是人工否决了危险操作，属正常拦截不算故障。'},
             {"role": "user", "content": brief}],
            response_format={"type": "json_object"})
        try:
            v = json.loads(msg.content)
        except json.JSONDecodeError:
            v = {"category": "无失败", "evidence": "解析失败"}
        classified.append({"run_id": r["run_id"], "question": r.get("question", "")[:40], **v})
        print(f"  {r['run_id']} → {v['category']}（{v['evidence'][:40]}）")

    # 汇总
    dist = {}
    for c in classified:
        dist[c["category"]] = dist.get(c["category"], 0) + 1
    lines = [f"# 失败模式归类报告  {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"总运行 {len(runs)} 次，需归类 {len(failures)} 次。", "",
             "| 类目 | 次数 |", "|---|---|"]
    for k, v in sorted(dist.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")
    lines += ["", "| run | 类目 | 依据 |", "|---|---|---|"]
    for c in classified:
        lines.append(f"| {c['run_id']} | {c['category']} | {c['evidence'][:60]} |")

    out = ROOT / "logs" / "failure_report.json"
    out.write_text(json.dumps({"dist": dist, "items": classified},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    md = ROOT / "logs" / "failure_report.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📊 已输出: {md}")


if __name__ == "__main__":
    import time
    main()

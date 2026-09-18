"""
评测运行器 v2：评测对象 = 当前生产内核（common/agent_core.py 的 ResearchAgent）。
跑评测集 → 逐题让 judge 打分（含证据核对）→ 聚合 Markdown 报告。

用法:
  .venv/bin/python evals/run_eval.py          # 全部12题（约30分钟）
  .venv/bin/python evals/run_eval.py --n 3    # 前3题快速验证
  .venv/bin/python evals/run_eval.py --id q02 q07   # 指定题目

产出: evals/results/eval_月日_时分.md（正式基线/回归对比用）
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from common.agent_core import ResearchAgent
from common.llm_client import make_client
from evals.judge import judge

QUESTIONS = ROOT / "evals" / "questions.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=99, help="只跑前N题")
    ap.add_argument("--id", nargs="*", help="指定题目id，如 q02 q07")
    args = ap.parse_args()

    bank = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    if args.id:
        bank = [q for q in bank if q["id"] in args.id]
    bank = bank[:args.n]

    client = make_client()
    rows, t_start = [], time.time()

    for i, q in enumerate(bank, 1):
        print(f"\n{'='*60}\n[{i}/{len(bank)}] {q['id']} {q['question']}")
        t0 = time.time()
        # 评测对象：当前生产内核（标准档预算）
        agent = ResearchAgent(impl="eval_production", run_id=f"eval_{q['id']}")
        r = agent.run(q["question"])
        scores = judge(client, q["question"], r.get("outline", {}),
                       r.get("evidence", []), r["answer"])
        secs = round(time.time() - t0)
        rows.append({**q, "scores": scores, "answer": r["answer"],
                     "tokens": r["tokens"], "seconds": secs})
        print(f"  总分 {scores.get('total')} | {secs}s | {r['tokens']} tok | "
              f"问题: {scores.get('issues', [])[:2]}")

    # ---- 聚合 ----
    scored = [r for r in rows if r["scores"].get("total") is not None]
    avg = sum(r["scores"].get("total", 0) for r in scored) / max(len(scored), 1)
    dims = ["supported", "sourced", "complete", "concise"]
    dim_avg = {d: round(sum(r["scores"].get(d, 0) for r in scored) / max(len(scored), 1), 2)
               for d in dims}
    total_tokens = sum(r["tokens"] for r in rows)
    total_secs = round(time.time() - t_start)

    lines = [f"# 正式基线评测报告  {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"**对象**: 生产内核 ResearchAgent（标准档 5万 tok）",
             f"**总平均分: {avg:.2f} / 1.00**　|　题数 {len(rows)}　|　总耗时 {total_secs}s　|　总 token {total_tokens:,}",
             f"**维度均分**: " + "　".join(f"{d}={dim_avg[d]:.2f}" for d in dims), "",
             "| 题号 | 题型 | 总分 | 有据 | 有源 | 覆盖 | 简洁 | 用时s | 主要问题 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        sc = r["scores"]
        lines.append(f"| {r['id']} | {r['type']} | **{sc.get('total')}** | "
                     f"{sc.get('supported')} | {sc.get('sourced')} | {sc.get('complete')} | "
                     f"{sc.get('concise')} | {'; '.join(sc.get('issues', [])[:2])[:90]} |")
    lines += ["", "---", "## 各题答案全文", ""]
    for r in rows:
        lines += [f"### {r['id']} {r['question']}",
                  f"> tokens {r['tokens']} · 用时 {r['seconds']}s", "",
                  r["answer"], ""]
    out = ROOT / "evals" / "results"
    out.mkdir(exist_ok=True)
    path = out / f"eval_{time.strftime('%m%d_%H%M')}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📊 基线报告已存: {path}")
    print(f"维度均分: {dim_avg}")


if __name__ == "__main__":
    main()

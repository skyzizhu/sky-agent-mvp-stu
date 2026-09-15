"""
评测运行器：跑评测集 → 逐题让judge打分 → 聚合成Markdown报告。

用法:
  .venv/bin/python evals/run_eval.py          # 跑全部12题（约20分钟）
  .venv/bin/python evals/run_eval.py --n 3    # 只跑前3题（快速验证，约5分钟）
  .venv/bin/python evals/run_eval.py --id q02 q07   # 指定题目

产出: evals/results/eval_月日_时分.md
之后每次改动（改prompt/换工具/调阈值）都重跑一遍对比总分——这就是agent的回归测试。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # evals/ 的上一层 = 项目根
sys.path.insert(0, str(ROOT))

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
    rows, start = [], time.time()
    for i, q in enumerate(bank, 1):
        print(f"\n{'='*60}\n[{i}/{len(bank)}] {q['id']} {q['question']}")
        t0 = time.time()
        # 延迟导入：避免评测脚本一启动就触发agent模块的副作用
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "v3", ROOT / "stages/05_structured_eval/research_agent_v3.py")
        v3 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(v3)

        r = v3.run(q["question"])
        scores = judge(client, q["question"], r["outline"], r["evidence"], r["answer"])
        rows.append({**q, "scores": scores, "answer": r["answer"],
                     "tokens": r["usage"], "seconds": round(time.time() - t0)})
        print(f"  总分 {scores.get('total')} | 用时{rows[-1]['seconds']}s | "
              f"问题: {scores.get('issues', [])[:2]}")

    # ---- 聚合报告 ----
    avg = sum(r["scores"].get("total", 0) for r in rows) / max(len(rows), 1)
    lines = [f"# 评测报告  {time.strftime('%Y-%m-%d %H:%M')}",
             f"\n**总平均分: {avg:.2f} / 1.00**　|　题数: {len(rows)}　|　"
             f"总耗时: {round(time.time()-start)}s\n",
             "| 题号 | 题型 | 总分 | 有据 | 有源 | 覆盖 | 简洁 | 主要问题 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["scores"]
        lines.append(f"| {r['id']} | {r['type']} | **{s.get('total')}** | "
                     f"{s.get('supported')} | {s.get('sourced')} | {s.get('complete')} | "
                     f"{s.get('concise')} | {'; '.join(s.get('issues', [])[:2])[:80]} |")
    lines += ["\n---\n## 各题答案全文\n"]
    for r in rows:
        lines += [f"### {r['id']} {r['question']}\n",
                  f"> tokens: {r['tokens']} | 用时: {r['seconds']}s\n",
                  r["answer"], ""]
    out = ROOT / "evals" / "results"
    out.mkdir(exist_ok=True)
    path = out / f"eval_{time.strftime('%m%d_%H%M')}.md"
    path.write_text("\n".join(lines))
    print(f"\n{'='*60}\n📊 总平均分 {avg:.2f} / 1.00　报告已存: {path}")


if __name__ == "__main__":
    main()

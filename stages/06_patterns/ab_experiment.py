"""
Stage 6 三方对比实验：纯 workflow / workflow+反思 / 自由 agent(v3)。

同一批评测题跑三种实现，用同一个 judge 打分，对比：质量分、耗时、token 总量。
用法:
  .venv/bin/python stages/06_patterns/ab_experiment.py --n 2   # 前2题（推荐先跑）
产出: evals/results/ab_月日_时分.md
"""
import argparse
import json
import sys
import time
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import llm_client
from common.llm_client import make_client
from evals.judge import judge

sys.path.insert(0, str(ROOT / "stages" / "06_patterns"))
import patterns


def load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2)
    args = ap.parse_args()

    bank = json.loads((ROOT / "evals/questions.json").read_text(encoding="utf-8"))["questions"]
    bank = bank[:args.n]
    client = make_client()
    v3 = load_module("v3", "stages/05_structured_eval/research_agent_v3.py")

    recorder = llm_client.CallRecorder()
    llm_client.RECORDER = recorder

    rows = []
    for i, q in enumerate(bank, 1):
        print(f"\n{'='*62}\n[{i}/{len(bank)}] {q['question']}")
        for label, runner in [
            ("A_纯workflow", lambda: patterns.run_workflow(q["question"])),
            ("B_反思增强", lambda: patterns.run_reflective(q["question"])),
            ("C_自由agent", lambda: v3.run(q["question"], max_steps=14)),
        ]:
            mark = len(recorder.calls)
            t0 = time.time()
            try:
                r = runner()
            except Exception as e:
                r = {"answer": f"运行失败: {e}", "evidence": [], "outline": {}}
            tokens = sum(c["response"]["usage"]["total_tokens"]
                         for c in recorder.calls[mark:])
            scores = judge(client, q["question"], r.get("outline", {}),
                           r.get("evidence", []), r["answer"])
            rows.append({"qid": q["id"], "label": label, "score": scores.get("total"),
                         "tokens": tokens, "seconds": round(time.time() - t0),
                         "issues": scores.get("issues", [])})
            print(f"  [{label}] 分{scores.get('total')} tok{tokens} "
                  f"{round(time.time()-t0)}s issues={len(scores.get('issues', []))}")

    # ---- 聚合：三种实现各自的平均分/均值 ----
    lines = [f"# 三方对比实验  {time.strftime('%Y-%m-%d %H:%M')}", "",
             "| 题目 | 实现 | 质量分 | tokens | 用时s | 主要问题 |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['qid']} | {r['label']} | **{r['score']}** | {r['tokens']} | "
                     f"{r['seconds']} | {'; '.join(r['issues'][:1])[:70]} |")
    lines += ["", "## 汇总（各实现平均）", "", "| 实现 | 平均质量分 | 平均tokens | 平均用时s |",
              "|---|---|---|---|"]
    for label in ["A_纯workflow", "B_反思增强", "C_自由agent"]:
        rs = [r for r in rows if r["label"] == label]
        if rs:
            lines.append(f"| {label} | "
                         f"{sum(r['score'] for r in rs)/len(rs):.2f} | "
                         f"{sum(r['tokens'] for r in rs)//len(rs)} | "
                         f"{sum(r['seconds'] for r in rs)//len(rs)} |")
    lines += ["", "> 判断框架：质量差距小→选便宜的；workflow质量塌了→agent或反思补位；"
              "反思轮次收益递减→限制max_rounds。数字只是这次题集的结果，换题集重跑。"]
    out = ROOT / "evals/results"
    out.mkdir(exist_ok=True)
    path = out / f"ab_{time.strftime('%m%d_%H%M')}.md"
    path.write_text("\n".join(lines))
    print(f"\n📊 报告已存: {path}")


if __name__ == "__main__":
    main()

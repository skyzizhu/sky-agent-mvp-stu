"""
Stage 7：主 agent（Orchestrator）——拆题、派工、验收、汇总。

流程：
  ① decompose: 一次LLM调用，把问题拆成 1~4 份任务书（effort scaling：简单问题不硬拆）
  ② dispatch:  ThreadPool 并行派出子agent（各自干净上下文+独立笔记）
  ③ collect:   收每份"浓缩结论"（子agent读几万token，只回传≤300字）
  ④ synthesize: 主agent汇总全部结论 → 最终对比报告

对外入口: run_multi_agent(question)
运行: .venv/bin/python stages/07_multi_agent/orchestrator.py
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from common.llm_client import make_client, call_llm


# ---------- ① 拆题（effort scaling：能不拆就不拆，最多4份） ----------
def decompose(client, question: str) -> dict:
    plan_msg, _ = call_llm(
        client,
        [{"role": "system",
          "content": '你是调研总调度。把用户问题拆成若干份"子任务书"交给并行调查员。'
                     '只输出JSON：{"strategy":"一句话拆分思路","subtasks":'
                     '[{"id":1,"task":"完整任务书：调研目标+要查证的具体信息+边界（不要碰其他子任务的范围）"}]}。'
                     '硬性规则：子任务1~4个；每个task必须自包含（调查员看不到用户原话）；'
                     '任务之间范围不得重叠；问题简单到一次调研能完成时就只给1个，不要硬拆。'},
         {"role": "user", "content": question}],
        response_format={"type": "json_object"},
    )
    return json.loads(plan_msg.content)


# ---------- ②③ 并行派工 + 收作业 ----------
def dispatch(client, subtasks: list, max_steps: int = 10):
    from sub_agent import run_sub_agent

    results = []
    wall_start = time.time()
    with ThreadPoolExecutor(max_workers=min(4, len(subtasks))) as pool:
        futures = {}
        for idx, st in enumerate(subtasks):
            # 错峰启动：避免所有worker同时打搜索接口触发限流
            futures[pool.submit(run_sub_agent, client, st, f"{st['id']}",
                                max_steps, stagger=idx * 5.0)] = st
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            print(f"    [worker{r['worker_id']}] {'✓' if r['ok'] else '✗'} "
                  f"{r['steps']}步/{r['tokens']}tok/{r['seconds']}s "
                  f"| 结论{len(r['findings'])}字")
    results.sort(key=lambda r: r["worker_id"])
    wall = round(time.time() - wall_start)
    serial = sum(r["seconds"] for r in results)
    print(f"    [并行收益] 墙钟 {wall}s vs 串行合计 {serial}s "
          f"(加速 {serial / wall:.1f}x)" if wall else "")
    return results, wall, serial


# ---------- ④ 汇总合成 ----------
def synthesize(client, question: str, results: list) -> str:
    reports = "\n\n".join(
        f"【调查员{r['worker_id']}·任务:{r['brief'][:40]}】\n{r['findings']}"
        for r in results)
    answer, _ = call_llm(
        client,
        [{"role": "system",
          "content": "你是主研究员。综合各位调查员的调研结论，回答用户的原始问题："
                     "输出结构化最终报告（可含对比表格），每个事实保留来源域名，"
                     "结论之间有冲突时如实指出，500字以内，简体中文。"
                     "调查员标注'未查到'的项如实说明，不许脑补。"},
         {"role": "user",
          "content": f"【用户原始问题】{question}\n\n【各调查员结论】\n{reports}"}])
    return answer.content  # call_llm 返回 message 对象，最终交付物是里面的文本


# ---------- 对外入口 ----------
def run_multi_agent(question: str) -> dict:
    client = make_client()
    t0 = time.time()

    print("  [①拆题]")
    plan = decompose(client, question)
    print(f"    策略: {plan['strategy']}")
    for st in plan["subtasks"]:
        print(f"    - 任务{st['id']}: {st['task'][:60]}")

    print(f"  [②③并行派工×{len(plan['subtasks'])}]")
    results, wall, serial = dispatch(client, plan["subtasks"])

    print("  [④汇总]")
    answer = synthesize(client, question, results)

    total = {"worker_tokens": sum(r["tokens"] for r in results),
             "wall_seconds": round(time.time() - t0), "subtasks": len(results),
             "wall_worker_gap": wall}
    return {"question": question, "strategy": plan["strategy"], "answer": answer,
            "workers": results, **total}


if __name__ == "__main__":
    q = "对比 Coze、Dify、文心智能体平台：各自定位是什么？收费模式是什么？"
    print(f"任务: {q}\n")
    r = run_multi_agent(q)
    print(f"\n{'='*62}\n📊 统计: {r['subtasks']}个并行子agent | "
          f"worker共{r['worker_tokens']}tok | 总墙钟{r['wall_seconds']}s")
    print(f"\n{'='*62}\n✅ 最终报告:\n{r['answer']}")

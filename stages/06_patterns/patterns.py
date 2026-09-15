"""
Stage 6：两种实现模式的对照品。

run_workflow(question)      —— 纯 workflow：固定管道，代码写死每一步
run_reflective(question)    —— workflow + Evaluator-Optimizer：草稿→评审→修订循环

和 agent（v3）的本质区别：流程由代码写死，LLM 只在每个工位上干活，
没有"决定下一步干什么"的权力。跑 ab_experiment.py 对比三者。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from common.llm_client import make_client, call_llm
from common.tools import web_search


# ---------- 纯 Workflow：四段固定管道，路径完全由代码决定 ----------
def run_workflow(question: str) -> dict:
    client = make_client()
    t0 = time.time()

    # 工位1（固定）：把问题变成3个搜索词——用模板拼接，不让模型自由发挥
    keywords = question.replace("？", "").replace("?", "")
    queries = [keywords, f"{keywords} 官网 价格", f"{keywords} 对比 评测"]

    # 工位2（固定）：逐个执行搜索（直接调工具函数，不经模型决策）
    evidence = []
    for q in queries:
        evidence.append(f"[搜索: {q}]\n{web_search(q)[:1500]}")

    # 工位3（固定）：把全部证据塞进一次调用，让模型写报告
    draft, _ = call_llm(client,
        [{"role": "system",
          "content": "你是研究助理。只依据【证据】写报告，每个事实标注来源域名，"
                     "400字以内，简体中文。证据不够就如实说'证据不足'，"
                     "并给来源加[n](url)引用编号。"},
         {"role": "user",
          "content": f"【问题】{question}\n\n【证据】\n" + "\n---\n".join(evidence)}])

    return {"question": question, "answer": draft, "evidence": evidence,
            "seconds": round(time.time() - t0)}


# ---------- Evaluator-Optimizer：草稿 → 评审 → 修订（最多2轮，可提前退出） ----------
def run_reflective(question: str, max_rounds: int = 2) -> dict:
    client = make_client()
    t0 = time.time()
    wf = run_workflow(question)
    draft, evidence = wf["answer"], wf["evidence"]
    history = [f"评审轮次的全部意见："]

    for round_i in range(1, max_rounds + 1):
        # 评审员（另一个视角的调用）：按rubric挑毛病，JSON输出
        crit_msg, _ = call_llm(client,
            [
                {"role": "system",
                 "content": '你是严格的评审员。对照【问题】和【证据】评审【草稿】，只输出JSON：'
                            '{"pass": true/false, "issues": ["问题1(必须具体、可修)", ...]}。'
                            '审查：事实是否有证据支撑、是否遗漏了问题的核心要求、引用是否可信。'
                            '草稿已达标时 pass=true。'},
                 {"role": "user",
                  "content": f"【问题】{question}\n【证据】\n" + "\n---\n".join(evidence)
                             + f"\n【草稿】\n{draft}"}],
            response_format={"type": "json_object"})
        verdict = json.loads(crit_msg.content)
        history.append(f"第{round_i}轮评审: pass={verdict['pass']} issues={verdict['issues']}")
        print(f"    [评审第{round_i}轮] pass={verdict['pass']} issues={len(verdict['issues'])}条")

        if verdict["pass"]:  # 提前退出：评审员放行
            break

        # 修订者：拿着意见改稿
        draft, _ = call_llm(client,
            [{"role": "system",
              "content": "根据评审意见修订报告。保持[n](url)引用格式，只解决提出的问题，"
                         "不要删掉已有的关键信息。"},
             {"role": "user",
              "content": f"【原草稿】\n{draft}\n\n【评审意见】\n"
                         + "\n".join(f"- {i}" for i in verdict["issues"])}])

    return {"question": question, "answer": draft, "evidence": evidence,
            "rounds": len(history), "review_log": history,
            "seconds": round(time.time() - t0)}


if __name__ == "__main__":
    q = "Notion 和飞书知识库的免费版权益有什么主要差异？"
    print(f"任务: {q}\n")
    print("--- 纯 Workflow ---")
    w = run_workflow(q)
    print(w["answer"][:200], "\n")
    print("--- Workflow + 反思 ---")
    r = run_reflective(q)
    print(f"共 {r['rounds']} 轮评审")
    print(r["answer"][:200])

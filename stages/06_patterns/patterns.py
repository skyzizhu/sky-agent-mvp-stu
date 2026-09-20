"""
Stage 6：两种实现模式的对照品。

run_workflow(question)      —— 纯 workflow：固定管道，代码写死每一步
run_reflective(question)    —— workflow + Evaluator-Optimizer（升级版）：
                               草稿 → 评审(只核对) → 修订(带工具：意见需要
                               新证据时先定向补查再改写) 循环

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
    draft_msg, _ = call_llm(client,
        [{"role": "system",
          "content": "你是研究助理。只依据【证据】写报告，每个事实标注来源域名，"
                     "400字以内，简体中文。证据不够就如实说'证据不足'，"
                     "并给来源加[n](url)引用编号。"},
         {"role": "user",
          "content": f"【问题】{question}\n\n【证据】\n" + "\n---\n".join(evidence)}])
    draft = (draft_msg.content or "").strip()   # ⚠取 .content：call_llm 返回的是消息对象

    return {"question": question, "answer": draft, "evidence": evidence,
            "seconds": round(time.time() - t0)}


# ---------- Evaluator-Optimizer：草稿 → 评审 → 修订（最多2轮，可提前退出） ----------
def run_reflective(question: str, max_rounds: int = 2, max_fix_searches: int = 2) -> dict:
    """workflow + Evaluator-Optimizer（升级版：修订者带工具）。

    与基础版的差别：评审员指出**事实层缺口**（缺数据/缺出处/覆盖不全）时，
    修订者不再闭卷硬改，而是先定向补查（每轮最多 max_fix_searches 次真实搜索），
    拿到新证据再改写——批评能被行动解决，而不是被措辞糊弄。
    补查结果同时并入证据池，下一轮评审员可见。
    """
    client = make_client()
    t0 = time.time()
    wf = run_workflow(question)
    draft, evidence = wf["answer"], wf["evidence"]
    history = ["评审轮次的全部意见："]
    fix_searches_total = 0

    for round_i in range(1, max_rounds + 1):
        # 评审员（另一个视角的调用）：只做核对——问题/证据/草稿三方一致性，不检索
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
        issues = verdict.get("issues", [])
        history.append(f"第{round_i}轮评审: pass={verdict['pass']} issues={issues}")
        print(f"    [评审第{round_i}轮] pass={verdict['pass']} issues={len(issues)}条")

        if verdict.get("pass"):  # 提前退出：评审员放行
            break

        # ★修订前置（升级点）：判断哪些意见需要"新的外部证据"才能修 → 定向补查
        #   判断权在模型（哪些要查、查什么），执行在代码（限次数、统一走 web_search）
        try:
            plan_msg, _ = call_llm(client,
                [{"role": "system",
                  "content": '你是修订策划。针对评审意见逐条判断：哪些需要"新的外部证据"才能修'
                             '（缺数据/缺出处/覆盖缺口），哪些改写即可（措辞/结构/删减）。'
                             '只输出JSON：{"searches": [{"issue": "意见原文", "query": "精准搜索词"}]}，'
                             f'最多 {max_fix_searches} 条；没有需要补查的就给空数组。'},
                 {"role": "user",
                  "content": f"【问题】{question}\n【评审意见】\n"
                             + "\n".join(f"- {i}" for i in issues)
                             + "\n【已有证据摘要】\n" + "\n---\n".join(e[-300:] for e in evidence)}],
                response_format={"type": "json_object"})
            searches = json.loads(plan_msg.content).get("searches", [])[:max_fix_searches]
        except Exception:
            searches = []   # 策划失败 → 降级为纯改写

        fix_evidence = []
        for s in searches:
            q = (s.get("query") or "").strip()
            if not q:
                continue
            print(f"    [修订补查] 意见「{(s.get('issue') or '')[:28]}」→ 搜索: {q}")
            evidence.append(f"[修订补查: {q}]\n{web_search(q)[:1500]}")
            fix_evidence.append(evidence[-1])
            fix_searches_total += 1
        if not fix_evidence:
            print("    [修订] 意见均可通过改写解决，无需补查")

        # 修订者：拿着意见 + 补查到的新证据改稿
        fix_block = ("\n\n【评审后补充检索的证据（优先用它修复对应意见，新事实加[n](url)引用）】\n"
                     + "\n---\n".join(fix_evidence)) if fix_evidence else ""
        rev_msg, _ = call_llm(client,
            [{"role": "system",
              "content": "根据评审意见修订报告。保持[n](url)引用格式，只解决提出的问题，"
                         "不要删掉已有的关键信息。"},
             {"role": "user",
              "content": f"【原草稿】\n{draft}\n\n【评审意见】\n"
                         + "\n".join(f"- {i}" for i in verdict["issues"]) + fix_block}])
        draft = (rev_msg.content or "").strip()   # ⚠取 .content：同上

    return {"question": question, "answer": draft, "evidence": evidence,
            "rounds": len(history), "review_log": history,
            "fix_searches": fix_searches_total,
            "seconds": round(time.time() - t0)}


if __name__ == "__main__":
    q = "Notion 和飞书知识库的免费版权益有什么主要差异？"
    print(f"任务: {q}\n")
    print("--- 纯 Workflow ---")
    w = run_workflow(q)
    print(w["answer"][:200], "\n")
    print("--- Workflow + 反思（修订者带工具） ---")
    r = run_reflective(q)
    print(f"共 {r['rounds']} 轮评审，修订期补查 {r['fix_searches']} 次检索")
    print(r["answer"][:200])

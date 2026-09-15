"""
LLM-as-judge：让一个模型按 rubric 给 agent 的答案打分。

设计（对应 Anthropic 多智能体研究的评测经验）:
- 单个 judge 比多个 judge 更稳定 → 用一次调用、一个分数
- 0.0~1.0 连续分数 + 四个子维度 + 理由，方便定位"哪里扣分"
- ★核心理念 groundedness：judge 拿到的不是"世界真相"，而是 agent 调研时的
  原始证据（工具返回）——检查"每个结论是否被证据支撑"，能抓编造

输出结构化JSON（用JSON mode强制），这是评测可聚合的前提。
"""
import json

import config

RUBRIC = """你是严格的研究质量评审员。对比【问题】【证据】（agent调研时工具的真实返回）
和【答案】（agent的最终报告），按四个维度打分（各0~1，总分=均值）：

1. supported（有据）：答案中的关键结论是否能在【证据】里找到支撑？编造/证据不足=0
2. sourced（有源）：答案是否带[n](url)引用编号？没有引用=0
3. complete（覆盖）：问题（及大纲）要求的信息点是否都覆盖？缺一半以上=0
4. concise（简洁）：是否有大量复述证据原文、空话套话？严重冗长=0.5以下

只输出JSON：
{"supported":0.x,"sourced":0.x,"complete":0.x,"concise":0.x,
 "total":0.x,"issues":["扣分原因1","扣分原因2"]}"""


def judge(client, question: str, outline: dict, evidence: list, answer: str) -> dict:
    content = (
        f"【问题】{question}\n\n"
        f"【大纲】{json.dumps(outline, ensure_ascii=False)[:800]}\n\n"
        f"【证据】（agent调研时工具的真实返回片段，共{len(evidence)}条）\n"
        + "\n---\n".join(evidence[:12]) + "\n\n"
        f"【答案】\n{answer}"
    )
    resp = client.chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "system", "content": RUBRIC},
                  {"role": "user", "content": content[:30000]}],
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(resp.choices[0].message.content)
    except json.JSONDecodeError:
        return {"supported": 0, "sourced": 0, "complete": 0, "concise": 0,
                "total": 0, "issues": ["judge输出解析失败"]}

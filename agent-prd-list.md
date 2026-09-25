# 标准 Agent PRD 需求 List 与产品经理输出深度指南

> 核心原则：
> **产品经理不需要说明 Agent 的代码怎么写，但必须把 Agent 应该怎么行动、什么时候行动、什么时候停止、失败以后怎么办写清楚。**
>
> 研发看完 PRD 后，可以继续讨论：
>
> * 用什么 Agent Framework（智能体框架）？
> * 怎么实现 State（状态）管理？
> * Tool（工具）怎么注册？
> * 怎么做异步执行？
>
> 但不应该继续问：
>
> * Agent 最多执行多少步？
> * 什么时候应该停止？
> * Tool 连续失败怎么办？
> * 用户点击 Stop 以后还要不要生成结果？
> * Budget 到多少开始收敛？
> * 什么时候必须人工确认？
> * Agent 能不能自己删除文件？
> * Context 太长怎么办？
> * LLM 请求失败重试几次？
>
> 后一类问题都应该由 PRD 提前定义。

---

# 一、标准 Agent PRD 总需求 List

一份完整的标准 Agent PRD，建议至少覆盖以下内容。

## A. 产品基础

1. 项目背景
2. 产品目标
3. 用户与使用场景
4. 产品范围 / 非范围
5. Agent 能力边界
6. Agent End-to-End Flow（端到端流程）
7. Run State Machine（任务运行状态机）

## B. 任务启动与规划

8. 用户任务输入
9. 输入校验
10. Session（会话）/ Task（任务）创建
11. Context 初始化
12. Memory（长期记忆）读取
13. Task Understanding（任务理解）
14. Planner（任务规划）
15. Plan Validation（计划校验）
16. Checklist / Task List（任务清单）
17. Priority（任务优先级）

## C. Agent 核心执行循环

18. Agent Loop（智能体执行循环）
19. Next Action Decision（下一步行动决策）
20. Tool Selection（工具选择）
21. Tool Permission（工具权限）
22. Tool Call（工具调用）
23. Tool Result（工具结果）
24. State Update（状态更新）
25. Progress Evaluation（进度判断）
26. Re-plan（重新规划）
27. Completion Judgment（完成判断）

## D. Tool（工具）与外部能力

28. Tool List（工具能力列表）
29. Tool 使用条件
30. Tool 参数错误
31. Tool Timeout（工具超时）
32. Tool Retry（工具重试）
33. 重复 Tool Call（重复工具调用）
34. Search Dedup（搜索去重）
35. URL / Resource Dedup（资源去重）
36. Domain / Provider Failure（来源失败）
37. Tool Result Quality（工具结果质量）
38. Tool Fallback（工具降级）

## E. Evidence / Notes / Context / Memory

39. Evidence（证据）
40. Evidence Verification（证据验证）
41. Conflict Handling（信息冲突处理）
42. Notes（任务笔记）
43. Context（上下文）
44. Context Budget（上下文预算）
45. Context Compaction（上下文压缩）
46. Memory（长期记忆）
47. Memory Write（记忆写入）
48. Memory Retrieval（记忆召回）
49. Memory Relevance（记忆相关性）

## F. Agent 收敛与终止

50. Step Limit（最大执行步数）
51. Budget（资源预算）
52. Progress Check（进展检查）
53. Soft Convergence（软收敛）
54. Hard Convergence（硬收敛）
55. Circuit Breaker（熔断）
56. No-progress Detection（无进展检测）
57. User Stop（用户停止）
58. Forced Finalization（强制结题）
59. Partial Success（部分成功）

## G. 安全与人工控制

60. Tool Risk Level（工具风险等级）
61. HITL / Human In The Loop（人工确认）
62. Approval（批准）
63. Reject（拒绝）
64. Approval Timeout（审批超时）
65. Irreversible Action（不可逆操作）
66. External Write（外部写操作）
67. Permission Boundary（权限边界）

## H. Final（最终输出）

68. Final Trigger（结题触发）
69. Final Report / Answer（最终报告 / 答案）
70. Citation / Source（引用 / 来源）
71. Incomplete Result（不完整结果）
72. Final Failure（最终生成失败）
73. Final Fallback（最终兜底）
74. Output Status（输出状态）

## I. 稳定性与异常

75. LLM Failure（大模型请求失败）
76. LLM Retry（大模型重试）
77. Backup Model（备用模型）
78. Structured Output Failure（结构化输出失败）
79. Context Failure（上下文处理失败）
80. Agent Protocol Failure（智能体协议失败）
81. System Failure（系统级失败）

## J. Observability（可观测性）

82. Execution Timeline（执行时间线）
83. Run Log（运行日志）
84. Stop Reason（停止原因）
85. Tool Metrics（工具指标）
86. Token / Cost（Token / 成本）
87. Debug Trace（调试链路）
88. User-visible Status（用户可见状态）

## K. AI 质量与验收

89. Eval Dataset（评测数据集）
90. Task Success Eval（任务成功评测）
91. Tool Use Eval（工具使用评测）
92. Groundedness Eval（有据性评测）
93. Completion Eval（完整度评测）
94. Safety Eval（安全评测）
95. Regression（回归评测）
96. E2E Acceptance（端到端验收）
97. GO / NO-GO Gate（上线 / 阻断发布门槛）

## L. 可选增强模块

98. Multi-Agent（多智能体）
99. Agentic RAG（智能体式 RAG）
100. Browser / Computer Use（浏览器 / 电脑操作）
101. Coding Agent（编程智能体）
102. MCP / External Tools（外部工具协议）
103. Scheduled Agent（定时智能体）
104. Long-running Agent（长任务智能体）
105. 优化建议

---

# 二、产品基础

# 1. 项目背景

需要回答：

> 为什么需要 Agent，而不是普通 Chatbot（聊天机器人）或者普通 Workflow（工作流）？

例如：

> 用户进行行业研究时，需要反复搜索、打开网页、提取信息、整理笔记和形成报告。
>
> 普通 Chatbot 只能一次性回答，无法主动执行多步任务。
>
> 本产品希望让 Agent 在用户给出目标后，自主规划并执行多步研究，最终输出带来源的研究结果。

不要只写：

> 因为 Agent 很火，所以做 Agent。

---

# 2. 产品目标

需要明确：

> Agent 最终帮用户完成什么任务。

例如：

> 用户只需要输入一个研究问题。
>
> Agent 自动完成：
>
> * 问题拆解；
> * 搜索；
> * 来源阅读；
> * 证据整理；
> * 交叉验证；
> * 最终报告。

同时要写：

> 不追求什么。

例如：

> V1 不支持自动发送邮件。
>
> V1 不支持直接购买商品。
>
> V1 不支持未经确认修改第三方系统数据。

---

# 3. 用户与使用场景

不要只写：

> 用户是知识工作者。

要写具体任务。

例如：

### 场景 1：研究

> “帮我研究目前主流 AI Coding Agent 的能力差异。”

### 场景 2：比较

> “帮我对比 Notion、飞书和 Confluence 的企业知识库能力。”

### 场景 3：执行

> “把这些资料整理成一份周报。”

### 场景 4：持续任务

> “每周检查竞品价格有没有变化。”

### 场景 5：Coding Agent

> “修复这个项目的登录 Bug，并跑测试确认。”

这些场景决定后续 Tool、权限和 Eval。

---

# 4. 产品范围 / 非范围

需要明确：

### V1 支持什么

例如：

* 多步任务执行；
* Tool Calling（工具调用）；
* Planning（规划）；
* Context（上下文）；
* Notes（任务笔记）；
* User Stop（用户停止）；
* Final Report（最终报告）。

### V1 不支持什么

例如：

* 自动付费；
* 自动删除生产数据；
* 未授权 Git Push；
* 自动发送外部消息；
* 高风险操作无人审批。

---

# 5. Agent 能力边界

这是 Agent PRD 必须有的章节。

要明确：

> Agent 能做什么。

例如：

* 搜索互联网；
* 打开网页；
* 读取文档；
* 整理信息；
* 写任务笔记；
* 调用代码执行工具；
* 生成报告。

同时要明确：

> Agent 不能做什么。

例如：

* 自行扩大权限；
* 绕过用户确认；
* 突破 Budget；
* 无限执行；
* 未经批准执行高风险写操作。

---

# 6. Agent End-to-End Flow（端到端流程）

正式 PRD 必须有完整主流程图。

例如：

```text
用户输入任务
→ Task Understanding（任务理解）
→ Planner（任务规划）
→ Checklist（任务清单）
→ Agent Loop（执行循环）
→ 判断下一步
→ Tool / Reasoning（工具 / 推理）
→ 获取结果
→ 更新 State / Notes（状态 / 笔记）
→ 判断任务是否完成
   ├─ 继续执行
   ├─ 换策略
   ├─ 重新规划
   ├─ 请求用户确认
   └─ 进入结题
→ Final（最终输出）
```

需要让研发看明白：

> Agent 不是一条固定流水线，而是循环执行。

---

# 7. Run State Machine（任务运行状态机）

正式 Agent 产品最好有状态机。

例如：

```text
Created（已创建）
↓
Planning（规划中）
↓
Researching / Executing（执行中）
↓
Compacting（上下文压缩中）
↓
Researching
↓
Waiting Approval（等待审批）
↓
Researching
↓
Finalizing（结题中）
↓
Completed（完成）
```

同时可能进入：

```text
Stopped（用户停止）
Failed（失败）
Partial Success（部分成功）
```

产品经理需要明确：

> 什么情况下状态发生变化。

---

# 三、任务启动与规划

# 8. 用户任务输入

需要定义：

* 空输入；
* 输入长度；
* 附件；
* 是否允许多目标；
* 是否允许直接要求执行；
* 是否需要用户选择模式。

例如：

> 空任务不可提交。
>
> 用户输入超过 X 字时仍允许提交，但系统可以先进行 Task Understanding（任务理解）。
>
> 用户可以选择 Quick / Standard / Deep 等执行模式。

---

# 9. 输入校验

需要定义：

> 什么任务系统不能直接执行。

例如：

* 信息不足；
* 权限不足；
* 请求超出 Agent 能力；
* 存在高风险操作；
* 用户目标明显冲突。

对于信息不足：

> 能合理推断时可以继续。

无法推断时：

> 应请求用户补充，而不是自行猜测关键条件。

---

# 10. Session（会话）/ Task（任务）创建

需要定义：

> 用户每次提交算新 Task 还是同一个 Session 的后续任务。

例如：

> 同一个 Session 可以包含多个连续任务。
>
> 每个独立执行任务生成一个 Run。

还需要定义：

> 后续任务可以继承哪些历史信息。

---

# 11. Context 初始化

任务开始时要明确：

> Agent 初始能看到什么。

例如：

* 用户当前任务；
* System Rules（系统规则）；
* 用户偏好；
* 当前 Session；
* 可用 Tool；
* Budget；
* 权限；
* 必要 Memory。

不能把所有历史信息无限塞给模型。

---

# 12. Memory（长期记忆）读取

如果产品支持 Memory，需要定义：

> 什么信息允许跨任务保存。

例如：

可以保存：

* 用户偏好中文；
* 喜欢表格输出；
* 关注价格；
* 常用工作方式。

不应该保存：

* 某一次搜索到的临时事实；
* 某次研究结论；
* 临时网页内容。

---

# 13. Task Understanding（任务理解）

需要明确：

Agent 应该理解：

* 用户目标；
* 输出形式；
* 时间范围；
* 对象；
* 约束；
* 成功标准。

例如：

> “对比三个 AI Coding Agent，重点看价格和企业能力。”

应提炼：

```text
目标：产品对比
对象：A / B / C
重点：价格、企业能力
输出：结构化报告
```

---

# 14. Planner（任务规划）

需要定义：

> Agent 是否必须先规划。

例如：

> 复杂任务默认先生成 Plan（计划）。
>
> 简单问题可以跳过 Planning。

如果是研究任务：

> 默认拆解 2～3 个核心子问题。

不要写：

> 拆得越多越详细。

过度拆解反而容易造成 Agent 不收敛。

---

# 15. Plan Validation（计划校验）

需要定义：

> 什么叫一个合格 Plan。

例如：

* 能覆盖用户核心目标；
* 子任务之间不明显重复；
* 子任务数量合理；
* 没有明显超出范围；
* 每个子任务可以被执行。

Plan 不合格：

> 允许重新生成 1 次。

仍失败：

> 使用 Minimal Plan（最小可用计划）继续。

---

# 16. Checklist / Task List（任务清单）

Agent 应该有明确的任务进度。

例如：

```text
Task 1：竞品价格
Pending（待执行）

Task 2：企业能力
In Progress（进行中）

Task 3：用户口碑
Completed（已完成）
```

需要定义：

> Completed（完成）不是“执行过”，而是已经获得足够结果。

---

# 17. Priority（任务优先级）

如果任务很多，需要定义优先级。

例如：

### P0

用户必须得到的核心结果。

### P1

重要补充。

### P2

有价值但可以在 Budget 不足时放弃。

进入 Soft Convergence（软收敛）后：

> 应优先完成 P0 / P1，不再扩展 P2。

---

# 四、Agent 核心执行循环

# 18. Agent Loop（智能体执行循环）

Agent 每轮通常包含：

```text
读取当前 State（状态）
→ 检查 Budget / Stop / Permission
→ 判断下一步
→ Tool / Reasoning
→ 获取结果
→ 更新 State
→ 判断是否继续
```

产品经理要定义循环边界。

---

# 19. Next Action Decision（下一步行动决策）

需要明确：

> 哪些事情允许模型自己决定。

例如：

模型可以决定：

* 搜什么；
* 看哪个来源；
* 用哪个低风险 Tool；
* 当前优先处理哪个 Checklist；
* 是否需要继续验证。

但不能决定：

* 最大 Step；
* 是否突破 Budget；
* 是否跳过用户权限；
* 是否绕过 HITL。

---

# 20. Tool Selection（工具选择）

需要定义：

> Agent 什么情况下应该选择 Tool，而不是直接回答。

例如：

> 当前问题需要实时信息，而 Context 中没有时，应优先 Search Tool。
>
> 已有足够 Evidence 时不应继续搜索。

---

# 21. Tool Permission（工具权限）

需要给 Tool 分风险级别。

例如：

### Low Risk（低风险）

* 搜索；
* 读取网页；
* 读取文件；
* 读取数据库。

可以自动执行。

### Medium Risk（中风险）

* 创建草稿；
* 修改临时文件；
* 运行代码。

根据产品决定是否确认。

### High Risk（高风险）

* 删除；
* 发送；
* 付费；
* 修改权限；
* 写生产系统。

必须 HITL（人工确认）。

---

# 22. Tool Call（工具调用）

产品经理需要定义：

> 什么情况下允许调用。

例如：

> 相同 Tool + 相同目标已经成功执行时，不应无意义重复调用。

Tool 参数必须完整。

如果模型生成的参数明显不合法：

> Tool 不执行。

---

# 23. Tool Result（工具结果）

需要定义：

> Tool 返回以后 Agent 应该看到什么。

至少要区分：

* Success（成功）
* Empty Result（空结果）
* Timeout（超时）
* Permission Denied（权限拒绝）
* Invalid Input（参数错误）
* Provider Error（服务异常）

不能把所有失败都返回成：

> “Error”。

否则 Agent 无法合理恢复。

---

# 24. State Update（状态更新）

每轮结束以后至少应该更新：

* 当前 Step；
* Checklist；
* Notes；
* Evidence；
* Budget；
* Tool Failure；
* Stop Reason；
* 当前状态。

产品经理不需要写 State Object（状态对象）的代码结构。

---

# 25. Progress Evaluation（进度判断）

不要只让 Agent 问：

> “我要不要继续？”

应该定义进展判断。

例如：

> 当前轮是否获得新的有效 Evidence？
>
> 是否完成一个 Checklist？
>
> 是否解决一个关键缺口？
>
> 是否只是重复已有信息？

如果多轮都没有新进展：

> 需要换策略或收敛。

---

# 26. Re-plan（重新规划）

什么情况下允许重新规划？

例如：

* 原 Plan 明显错误；
* 新证据改变任务方向；
* 某条路径不可执行；
* 用户追加要求；
* 任务拆分明显不合理。

Re-plan 不应该：

> 每执行一步就重新生成全部 Plan。

---

# 27. Completion Judgment（完成判断）

必须定义：

> 什么叫任务完成。

例如：

满足以下条件可以进入 Final：

* P0 Checklist 全部完成；
* 用户核心目标已经覆盖；
* 关键 Evidence 足够；
* 没有必须继续确认的缺口。

不能仅仅因为：

> “模型觉得够了。”

---

# 五、Tool（工具）与外部能力

# 28. Tool List（工具能力列表）

PRD 中需要列出：

> Agent 拥有哪些能力。

例如 Research Agent：

* Search（搜索）
* Fetch URL（网页读取）
* Note Write（写笔记）
* Note Read（读笔记）

Coding Agent：

* Code Search（代码搜索）
* File Read（文件读取）
* File Edit（文件编辑）
* Terminal（终端执行）
* Test（测试）

不用写 Tool Schema（工具结构）。

---

# 29. Tool 使用条件

例如：

Search Tool：

> 当前 Evidence 不足时使用。

Fetch Tool：

> Search Snippet（搜索摘要）不足以支持结论时使用。

Note Tool：

> 获得重要、需要跨轮保留的信息时使用。

---

# 30. Tool 参数错误

如果 Agent 生成：

> 非法 JSON、缺少必要参数、错误文件路径。

系统应该：

> 不执行 Tool。

返回：

> 参数错误 + 可修复提示。

允许模型重新生成。

---

# 31. Tool Timeout（工具超时）

需要定义确定值。

例如：

> Search Timeout = 15 秒。
>
> Fetch Timeout = 20 秒。
>
> Browser Action Timeout = 30 秒。

具体值应根据业务实际确定。

---

# 32. Tool Retry（工具重试）

不能所有 Tool 无限重试。

例如：

> 网络型 Tool 默认最多自动重试 2 次。
>
> 权限错误不重试。
>
> 参数错误交给模型修复，不做相同请求重试。

---

# 33. 重复 Tool Call（重复工具调用）

需要定义：

> 相同输入成功执行以后，应优先复用结果。

避免：

```text
Search A
Search A
Search A
Search A
```

无限重复。

---

# 34. Search Dedup（搜索去重）

例如：

> 完全相同 Query（查询）直接复用历史结果。
>
> 高度相似 Query 应优先使用已有信息或改变搜索角度。

如果产品已经有明确阈值：

> Similarity（相似度）≥0.75 视为高度重复。

可以直接写进 PRD。

---

# 35. URL / Resource Dedup（资源去重）

例如：

> 同一个规范化 URL 已经成功 Fetch 后，本 Run 再次访问不重新请求网络。

直接：

> 使用已缓存内容。

如果用户后续明确要求“最新”：

> 可以根据时效规则重新 Fetch。

---

# 36. Domain / Provider Failure（来源失败）

例如：

> 同一个 Domain（域名）连续失败达到 2 次后，Agent 不应继续尝试同域多个页面。

应该：

> 换其他来源。

---

# 37. Tool Result Quality（工具结果质量）

成功并不等于有用。

例如 Search 返回：

> 5 条完全无关内容。

虽然 Tool Call 成功，但应该记录：

> No Useful Evidence（无有效信息）。

这会影响 Progress Evaluation（进展判断）。

---

# 38. Tool Fallback（工具降级）

例如：

> 静态网页读取失败
> → Browser Fetch（浏览器抓取）
> → 仍失败
> → 换来源。

Coding Agent：

> Test Tool 不可用
> → 使用已有静态检查结果
> → Final 中明确测试未执行。

---

# 六、Evidence / Notes / Context / Memory

# 39. Evidence（证据）

Agent 不应该把所有 Tool Result 都当 Evidence。

Evidence 应该是：

> 能够支持用户问题中的某个事实、结论或行动判断的信息。

例如：

> 官网价格页面证明价格。

而：

> 搜索结果标题只是 Candidate（候选信息）。

---

# 40. Evidence Verification（证据验证）

需要定义：

> 什么事实需要几个来源。

例如 Research Agent：

### A 类关键事实

* 价格；
* 日期；
* 规格；
* 政策；
  -限制。

满足：

> 1 个官方一手来源。

如果没有官方来源：

> ≥2 个独立二手来源。

---

# 41. Conflict Handling（信息冲突处理）

如果不同来源冲突：

> Agent 不允许自行隐藏冲突。

应该：

> 标记冲突。

最终说明：

> 来源 A 怎么说；
> 来源 B 怎么说；
> 当前为什么无法确认。

---

# 42. Notes（任务笔记）

Notes 用来保存：

> 当前任务中需要跨轮持续保留的重要信息。

例如：

* 已确认事实；
* 来源；
* 当前结论；
* 未解决问题。

Notes 不等于 Chat History（聊天历史）。

---

# 43. Context（上下文）

Context 是：

> 当前这一轮 LLM 真正看到的信息。

产品经理要定义：

* 什么一定要保留；
* 什么可以压缩；
* 什么不能进入；
* Context 超限怎么办。

---

# 44. Context Budget（上下文预算）

例如：

> Context 超过 4000 / 8000 / 指定 Token 后进入压缩。

具体值根据项目模型和成本确定。

但正式 PRD 最终必须有明确值。

---

# 45. Context Compaction（上下文压缩）

压缩必须保留：

* 当前 Goal（目标）；
* Plan；
* Checklist；
* 已确认 Evidence；
* Notes；
* 用户约束；
* System Rule；
* 最近必要对话。

不能因为压缩：

> 把已经确认的重要事实丢掉。

---

# 46. Memory（长期记忆）

Memory 用于：

> 跨任务、跨 Session 保存稳定信息。

例如：

* 用户喜欢中文；
* 用户喜欢表格；
* 用户更关注成本；
* 用户的固定项目偏好。

---

# 47. Memory Write（记忆写入）

需要定义：

> 什么才允许写入长期 Memory。

例如：

只有：

> 稳定偏好、长期事实、重复出现的信息。

不保存：

> 临时搜索结果。

---

# 48. Memory Retrieval（记忆召回）

任务开始时：

> 不应该把所有 Memory 全部塞进 Context。

应该只召回：

> 和当前任务相关的信息。

---

# 49. Memory Relevance（记忆相关性）

例如：

用户曾经说：

> “比较 SaaS 时我特别关注价格。”

当前任务：

> “研究 Notion 定价。”

可以召回。

当前任务：

> “帮我写 Python 排序算法。”

不应该召回。

---

# 七、Agent 收敛与终止

# 50. Step Limit（最大执行步数）

必须有上限。

例如：

> Max Step（最大步骤）= 20。

这是防止无限 Agent Loop 的基础边界。

---

# 51. Budget（资源预算）

Budget 可以是：

* Token；
* Cost（费用）；
* Tool Call 数量；
* 时间；
  -执行步数。

例如：

> Standard Budget = 50,000 Tokens。

内部 Planner、Compaction、Final 等调用是否计入：

> 必须明确。

通常建议：

> 全部计入。

---

# 52. Progress Check（进展检查）

例如：

> Step ≥4 或 Budget ≥50% 后，每轮执行 Progress Check。

检查：

* 是否获得新 Evidence；
* 是否推进 Checklist；
* 是否重复；
* 是否应该换策略。

---

# 53. Soft Convergence（软收敛）

例如：

> Budget ≥80% 后进入 Soft Convergence。

进入以后：

> 不再扩展新的 P2 任务。

只允许：

* 补核心缺口；
* 完成 P0 / P1；
* 准备 Final。

---

# 54. Hard Convergence（硬收敛）

例如：

> Budget ≥95% 后，研究类 Tool 不再提供给模型。

Agent：

> 只能基于当前 Evidence / Notes 结题。

这比 Prompt 里告诉模型：

> “请不要继续搜索。”

更可靠。

---

# 55. Circuit Breaker（熔断）

例如：

> 连续 4 次 Tool / LLM 执行失败触发 Circuit Breaker。

触发后：

> 不允许继续完全相同路径。

必须：

* 换 Tool；
* 换 Source；
* 换 Query；
* 使用已有 Evidence；
* 或收敛。

---

# 56. No-progress Detection（无进展检测）

失败和“没进展”不是同一件事。

例如：

连续 3 轮都成功 Search，

但得到的内容全部重复。

这种也应该触发：

> Strategy Change（策略切换）或者 Convergence（收敛）。

---

# 57. User Stop（用户停止）

用户 Stop 后：

> 不应立即粗暴丢弃已有结果。

正确流程：

```text
用户 Stop
→ 停止新的 Tool
→ 保留 Notes / Evidence
→ 进入 Final
→ 输出阶段性结果
```

---

# 58. Forced Finalization（强制结题）

满足以下任何条件可以强制 Final：

* Budget 用尽；
* Max Step 到达；
* Circuit Breaker 超限；
* 用户 Stop；
* Main Agent 无法继续；
* Checklist 基本完成。

---

# 59. Partial Success（部分成功）

Agent 产品非常需要这个状态。

例如：

> 用户要求研究 5 个对象，Agent 完成 4 个，第 5 个没有可靠信息。

可以定义：

> Partial Success（部分成功）。

而不是简单 Failure（失败）。

---

# 八、安全与人工控制

# 60. Tool Risk Level（工具风险等级）

每个 Tool 应该有产品风险等级。

例如：

### R0：只读

搜索、读取。

### R1：可撤销写入

创建草稿、临时文件。

### R2：有影响写入

修改文件、创建记录。

### R3：高风险

删除、发送、支付、权限修改。

---

# 61. HITL / Human In The Loop（人工确认）

高风险 Tool 必须进入人工确认。

需要展示：

* Agent 想做什么；
* 使用什么 Tool；
* 参数；
* 影响；
* 风险。

用户选择：

* Approve（批准）
* Reject（拒绝）

---

# 62. Approval（批准）

用户批准后：

> 只允许执行本次批准的具体动作。

不能理解成：

> 用户批准一次以后后续所有危险操作都自动允许。

---

# 63. Reject（拒绝）

用户拒绝后：

> Tool 不执行。

Agent 应收到：

> 用户已拒绝。

并继续其他可执行任务。

不能：

> 立即换一种参数重复请求同一个危险动作。

---

# 64. Approval Timeout（审批超时）

需要明确时间。

例如：

> HITL 最长等待 10 分钟。

超过：

> 默认按 Reject 处理。

---

# 65. Irreversible Action（不可逆操作）

例如：

* 删除生产数据；
* 提交订单；
* 发布内容；
* Push 到生产分支。

必须单独控制。

---

# 66. External Write（外部写操作）

例如：

* 发邮件；
* 发 Slack；
* 修改 CRM；
* 创建 Git PR；
* 修改文件。

需要明确：

> 哪些允许自动做，哪些必须审批。

---

# 67. Permission Boundary（权限边界）

Agent 永远不能：

> 获得比当前用户更高的权限。

用户无权访问：

> Agent 也不能访问。

---

# 九、Final（最终输出）

# 68. Final Trigger（结题触发）

明确哪些情况进入 Final：

* 正常完成；
* Soft/Hard Convergence；
* User Stop；
* Budget Exhausted；
* Max Step；
* Circuit Breaker；
* 系统异常。

---

# 69. Final Report / Answer（最终报告 / 答案）

产品经理要定义：

> 最终输出长什么样。

例如 Research Agent：

* Executive Summary（摘要）
* Key Findings（关键发现）
* 子问题结果
* Evidence / Citation
* 冲突
* 未确认信息
* Sources（来源）

Coding Agent：

* 完成了什么；
* 修改了哪些文件；
* 测试结果；
* 未完成项；
* 风险。

---

# 70. Citation / Source（引用 / 来源）

如果 Agent 基于外部资料做事实判断：

> 关键事实应该有来源。

不能：

> 报告里出现大量数字但无法追踪来源。

---

# 71. Incomplete Result（不完整结果）

任务未完全完成时：

> 必须明确告诉用户。

例如：

> “以下为阶段性结果，其中企业权限部分尚未获得可靠资料。”

不能假装：

> 所有任务全部完成。

---

# 72. Final Failure（最终生成失败）

如果 Final LLM 请求失败：

> 不能把已经获得的 Evidence 全部丢掉。

---

# 73. Final Fallback（最终兜底）

推荐：

```text
正常 Final
→ 失败
→ No-Tools Forced Final（无工具强制生成）
→ 失败
→ Notes Fallback（任务笔记兜底）
→ Notes 也为空
→ 明确失败提示
```

---

# 74. Output Status（输出状态）

最终至少区分：

* Success（成功）
* Partial Success（部分成功）
* Stopped（用户停止）
* Failed（失败）

方便用户和内部统计。

---

# 十、稳定性与异常

# 75. LLM Failure（大模型请求失败）

需要区分：

* Timeout（超时）
* 429（限流）
* 5xx（服务异常）
* Authentication Error（认证错误）
* Invalid Response（非法响应）

不同错误不应该全部同样处理。

---

# 76. LLM Retry（大模型重试）

例如：

> Timeout / 429 / 5xx 最多请求 3 次。

第 2 次：

> 等待 2 秒。

第 3 次：

> 等待 5 秒。

认证错误：

> 不自动重试。

---

# 77. Backup Model（备用模型）

主模型失败以后：

> 可以调用 Backup Model 1 次。

但是 Backup Model 必须：

> 具备当前节点要求的能力。

例如节点要求 Tool Calling：

> Backup Model 也必须支持 Tool Calling。

---

# 78. Structured Output Failure（结构化输出失败）

例如 Planner 要输出结构化 Plan。

如果 JSON / Schema 不合法：

> 允许修复 / 重试 1 次。

仍失败：

> 使用 Minimal Plan。

不能：

> 因为一个 JSON 少逗号整个 Agent 结束。

---

# 79. Context Failure（上下文处理失败）

Compaction 失败：

> 使用确定性降级 Context。

至少保留：

* Goal；
* Checklist；
* Notes；
* 用户约束；
* 最近消息。

---

# 80. Agent Protocol Failure（智能体协议失败）

例如：

模型输出了 Tool Call，

但 Tool Result 没有正确对应。

系统应该：

> 修复或安全停止。

不能继续让上下文进入异常状态。

---

# 81. System Failure（系统级失败）

例如：

* API Key 无效；
* 核心 Provider 不可用；
* 配置缺失；
* 权限系统异常。

这类错误应该：

> 直接向用户说明系统不可用。

而不是让 Agent 无意义重试 20 Step。

---

# 十一、Observability（可观测性）

# 82. Execution Timeline（执行时间线）

用户侧可以看到简化流程。

例如：

```text
正在分析问题
正在制定计划
正在搜索资料
正在阅读网页
正在核实信息
正在整理报告
```

不要把内部 Chain of Thought（思维链）直接展示给用户。

---

# 83. Run Log（运行日志）

内部需要知道：

* Run ID；
* Session；
* Start / End；
* Step；
* Tool；
* Error；
* Token；
* Cost；
* Stop Reason。

---

# 84. Stop Reason（停止原因）

至少区分：

* model_done（模型完成）
* checklist_done（任务完成）
* budget_limit（预算限制）
* max_steps（最大步骤）
* user_stop（用户停止）
* circuit_breaker（熔断）
* error（系统异常）

---

# 85. Tool Metrics（工具指标）

至少可以统计：

* Tool Call 次数；
* 成功率；
* Failure Rate（失败率）；
* Cache Hit（缓存命中）；
* Latency（耗时）。

---

# 86. Token / Cost（Token / 成本）

需要统计：

> 一个 Run 到底花了多少 Token / Cost。

并且：

> Planner、Main Agent、Compaction、Final 等是否全部计入。

---

# 87. Debug Trace（调试链路）

内部至少可以还原：

```text
模型当时看到什么
→ 做了什么决定
→ 调用了什么 Tool
→ Tool 返回什么
→ 为什么下一步这样走
```

不用向最终用户展示全部内部信息。

---

# 88. User-visible Status（用户可见状态）

用户不需要看到：

> agent_core.step_14。

而应该看到：

> 正在核实价格信息。

状态需要：

> 对用户有意义。

---

# 十二、AI 质量与验收

# 89. Eval Dataset（评测数据集）

Agent 应有固定测试任务。

至少覆盖：

* 简单任务；
* 多步骤任务；
* 需要 Tool；
* Tool 失败；
* 无结果；
* 用户 Stop；
* 高风险操作；
* 长 Context；
* Budget 接近上限；
* 多轮任务。

---

# 90. Task Success Eval（任务成功评测）

需要判断：

> 用户要求的事情到底有没有完成。

不是：

> API 返回 200 就算成功。

---

# 91. Tool Use Eval（工具使用评测）

评估：

* Tool 选对了吗；
* 参数合理吗；
* 是否重复；
* 是否在不该调用时调用；
* 危险 Tool 是否绕过审批。

---

# 92. Groundedness Eval（有据性评测）

对于 Research / Knowledge Agent：

> 最终事实是否被 Evidence 支撑。

不能：

> Agent 搜了很多资料，但最终结论自己编。

---

# 93. Completion Eval（完整度评测）

例如用户要求：

> 对比价格、功能、安全性。

结果只回答价格和功能：

> 不算完整完成。

---

# 94. Safety Eval（安全评测）

需要检查：

* 是否绕过 HITL；
* 是否执行未授权动作；
* 是否突破权限；
* 是否违反数据边界。

---

# 95. Regression（回归评测）

任何影响 Agent 行为的改动都需要重新 Eval。

例如：

* Prompt；
* Model；
* Tool Description；
* Tool Set；
* Planner；
* Context；
* Memory；
* Budget；
* Loop；
* Retry Strategy（重试策略）。

例如：

> 任一核心 Eval 指标下降 >0.05 → NO-GO。

---

# 96. E2E Acceptance（端到端验收）

最终必须跑完整任务。

例如 Research Agent：

```text
用户输入
→ Planner
→ Search
→ Fetch
→ Evidence
→ Notes
→ Checklist
→ Final
→ Citation
```

还要覆盖：

```text
Tool Failure
User Stop
Budget Limit
LLM Failure
HITL
Context Compaction
```

---

# 97. GO / NO-GO Gate（上线 / 阻断发布门槛）

最终版本要明确：

> 什么条件下可以发布。

例如：

```text
30 个 E2E Run 全部进入明确终态
Success + Partial Success ≥ 90%
无限循环 = 0
Budget 越界 = 0
Stop 失效 = 0
未授权高风险操作 = 0
空白 Final = 0
关键事实无 Evidence = 0
核心 Eval 指标 Regression >0.05 = 0
```

满足：

**GO（允许发布）**

否则：

**NO-GO（禁止发布）**

---

# 十三、可选增强模块

# 98. Multi-Agent（多智能体）

如果一个 Manager（管理智能体）分配多个 Worker（工作智能体），需要额外定义：

* 什么情况下拆 Worker；
* 最多几个 Worker；
* Worker 能不能继续创建 Worker；
* Budget 怎么分；
* Worker 信息如何汇总；
* Worker 冲突怎么办；
* Manager 是否可以覆盖 Worker 结论。

---

# 99. Agentic RAG（智能体式 RAG）

需要增加：

> Agent 可以自主决定是否再次检索。

需要定义：

* 最大检索轮数；
* 什么叫 Evidence 不够；
* 什么时候必须停止；
* Query 怎么变化；
* 如何防止重复 Retrieval（检索）。

---

# 100. Browser / Computer Use（浏览器 / 电脑操作）

需要额外定义：

```text
Observe（观察）
→ Decide（判断）
→ Action（操作）
→ Verify（验证）
```

特别需要定义：

* 页面变化后旧状态是否失效；
* 点击以后是否要验证；
* 表单提交前是否需要确认；
* 支付 / 删除 / 发布怎么审批。

---

# 101. Coding Agent（编程智能体）

需要额外定义：

```text
任务理解
→ Repo Context（代码仓库上下文）
→ Code Search（代码检索）
→ Read（读取）
→ Plan
→ Edit（修改）
→ Test（测试）
→ Failure Diagnosis（失败诊断）
→ Repair Loop（修复循环）
→ Diff Review（变更检查）
→ Final
```

还要定义：

* 最大 Repair 次数；
* 是否允许 Git Commit；
* 是否允许 Push；
* 危险终端命令；
* 测试失败是否允许交付；
* 修改范围是否允许扩大。

---

# 102. MCP / External Tools（外部工具协议）

如果支持动态外部 Tool：

需要定义：

* 哪些 MCP Tool 自动可用；
* 哪些需要确认；
* Tool 权限等级；
* Tool 不可用怎么办；
* Tool 动态变化以后 Agent 怎么表现。

---

# 103. Scheduled Agent（定时智能体）

需要额外定义：

* 执行频率；
* 用户时区；
* 任务失败是否重试；
* 连续失败怎么办；
* 是否发送通知；
* 任务什么时候自动停。

---

# 104. Long-running Agent（长任务智能体）

需要额外定义：

* 任务最长运行时间；
* 中间状态保存；
* 断线恢复；
* Resume（继续执行）；
* 状态过期；
* 用户什么时候可以取消。

---

# 105. 优化建议

PRD 最后可以增加：

## 可选优化建议

例如：

| 优先级 | 当前问题                | 建议                               | 预期收益      |
| --- | ------------------- | -------------------------------- | --------- |
| P1  | Agent 重复搜索较多        | 增加 Search Semantic Dedup（语义搜索去重） | 降低成本、减少循环 |
| P1  | Context 过长后信息损失     | 优化 Context Compaction（上下文压缩）     | 提升长任务稳定性  |
| P1  | Tool Failure 只看失败次数 | 增加 No-progress Detection（无进展检测）  | 更早识别无效循环  |
| P2  | Planner 一次性生成       | 增加动态 Re-plan（重新规划）能力             | 提高复杂任务适应性 |

必须明确：

> **优化建议不属于当前版本正式需求。**

---

# 十四、AI 产品经理写 Agent PRD 最简单的检查方法

写完以后，逐条问自己：

### 用户到底要 Agent 完成什么？

说清楚了吗？

### Agent 从开始到结束怎么运行？

说清楚了吗？

### Agent 是固定流程还是循环？

说清楚了吗？

### 哪些事情模型可以自主决定？

说清楚了吗？

### 哪些事情必须系统控制？

说清楚了吗？

### Agent 有什么 Tool？

说清楚了吗？

### Tool 什么时候可以用？

说清楚了吗？

### Tool 失败怎么办？

说清楚了吗？

### 连续失败怎么办？

说清楚了吗？

### 重复操作怎么办？

说清楚了吗？

### Evidence（证据）怎么判断？

说清楚了吗？

### 来源冲突怎么办？

说清楚了吗？

### Context（上下文）太长怎么办？

说清楚了吗？

### Notes（任务笔记）和 Memory（长期记忆）区别是什么？

说清楚了吗？

### Agent 最多执行多少 Step？

说清楚了吗？

### Budget 到多少开始收敛？

说清楚了吗？

### Budget 到多少彻底不允许继续 Tool？

说清楚了吗？

### 用户 Stop 怎么办？

说清楚了吗？

### 危险操作什么时候需要 HITL？

说清楚了吗？

### 用户 Reject 后怎么办？

说清楚了吗？

### LLM 请求失败重试几次？

说清楚了吗？

### Backup Model 失败以后怎么办？

说清楚了吗？

### Final 失败以后已有结果会不会丢？

说清楚了吗？

### 什么叫 Success？

说清楚了吗？

### 什么叫 Partial Success？

说清楚了吗？

### 什么情况一票否决？

说清楚了吗？

### 什么条件才能 GO？

说清楚了吗？

如果这些问题研发仍然大量回来问产品：

> PRD 还没有写完整。

---

# 十五、最后总结：Agent 产品经理真正需要交付什么

可以把标准 Agent PRD 最终归纳为九件事。

## 1. 定义目标

> Agent 最终要帮用户完成什么。

## 2. 定义流程

> Agent 从任务开始到最终输出怎么运行。

## 3. 定义自主权

> 哪些事情模型可以自己决定。

## 4. 定义边界

> 哪些事情必须由系统、权限或用户控制。

## 5. 定义 Tool

> Agent 可以使用什么能力，以及什么时候能用。

## 6. 定义状态

> Context、Notes、Memory、Checklist 如何影响 Agent。

## 7. 定义收敛

> Agent 什么时候继续、什么时候换策略、什么时候必须停止。

## 8. 定义失败路径

> LLM、Tool、Context、Final 等失败以后产品怎么表现。

## 9. 定义质量

> 什么叫任务完成、什么叫 Agent 做得好、什么条件才能上线。

最终仍然可以用这句话判断：

> **凡是会影响用户体验、Agent 行为、安全、权限、成本、任务结果或验收结论的事情，产品经理都应该定义。**

而：

> **内部到底用 LangGraph、Python、Redis、什么 State Class、什么异步机制、什么数据库、什么代码结构实现，交给研发。**

AI 产品经理真正要做到的是：

> **研发可以决定“怎么实现”，但不需要猜“应该实现成什么样”。**

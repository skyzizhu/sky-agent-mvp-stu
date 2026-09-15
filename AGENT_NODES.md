# 节点详解 —— Agent 每个节点的学习手册

> 用途：这是你最重要的复习材料。每个节点按统一格式记录：**目标 / 作用 / 怎么做 / 输入 / 输出 / 着重注意**。
> 配合代码看：每个节点都标注了对应的代码位置。
> 状态标记：✅ 已实现（Stage 0–2）｜🚧 规划中（Stage 3+，边做边补全本文件）

## 全局视图：这些节点如何组成一个 Agent

```
                        ┌─────────────────────────────┐
                        │      N6 Agent Loop 控制器     │◄─────────┐
                        │  (循环 + 两个停止条件)         │          │
                        └──────┬──────────────▲───────┘          │
                               │ 下一步做什么？  │ 工具结果回填(N5)   │
                               ▼              │                  │
                    ┌────────────────┐   ┌───┴──────────┐       │
                    │ N1 模型调用节点  │   │ N4 工具执行节点 │──────┘
                    │ (chat completions)│  │ (dispatch)   │
                    └──────┬─────────┘   └──────────────┘
                           ▲│
             对话历史(messages)│└─工具声明(TOOLS)
                           ▼
                    ┌────────────────────┐      ┌──────────────┐
                    │ N3 消息管理节点      │      │ N7 Trace 日志 │
                    │ (唯一的状态载体)     │      │ (可回放)      │
                    └────────────────────┘      └──────────────┘
```

一句话记住：**messages 是状态，模型是大脑，dispatch 是手，loop 是心跳，trace 是病历。**

---

## ✅ 节点 0：配置与模型接入

**代码**：`config.py` + `.env`

| 项 | 内容 |
|---|---|
| 目标 | 让 agent 能连上一个云端 LLM，且换模型不改代码 |
| 作用 | 所有阶段的公共地基 |
| 怎么做 | 走 OpenAI 兼容协议（Chat Completions），三要素 `API_KEY / BASE_URL / MODEL` 放在 `.env`，代码只读环境变量 |
| 输入 | `.env` 里的三个环境变量 |
| 输出 | 可用的 `OpenAI` client 实例 |
| 着重注意 | ① 模型必须**支持 tool calling**，否则 Stage 1 之后全部失效；② key 不要写进代码或提交到 git（`.env` 加入 `.gitignore`）；③ 先用便宜模型调试，跑通后再换强模型对比 |

---

## ✅ 节点 1：模型调用节点（chat completion）

**代码**：`common/llm_client.py` 的 `call_llm()`

| 项 | 内容 |
|---|---|
| 目标 | 把对话历史交给模型，拿回它的下一步决策 |
| 作用 | agent 的"大脑"，循环中每转一圈就调用一次 |
| 怎么做 | `client.chat.completions.create(model, messages, tools)`；本项目的封装在 `call_llm()`，返回 `(assistant消息对象, token用量)` |
| 输入 | `messages`：完整历史数组；`tools`：工具声明（可选） |
| 输出 | assistant 消息：**二选一**——`content`（想说话了=任务完成或中间思考）或 `tool_calls`（要调用工具）；外加 `usage`（token 消耗） |
| 着重注意 | ① **API 是无状态的**：每次请求都要重发全部历史，所以 input token 随轮数线性增长（Stage 0 的实验就是让你亲眼看这件事）；② 一次返回可能包含**多个** tool_calls（并行调工具）；③ 返回什么由模型决定，你的代码必须两种情况都能处理 |

---

## ✅ 节点 2：消息管理节点（messages 状态）

**代码**：Stage 0–2 各脚本的 `messages` 列表操作

| 项 | 内容 |
|---|---|
| 目标 | 维护 agent 运行期的全部状态 |
| 作用 | messages 是**唯一的状态载体**——模型的每一个决策都基于这个数组 |
| 怎么做 | 一个列表按时间顺序 append 四种消息：`system`（人设/规则，放最前且只放一次）→ `user`（任务）→ `assistant`（模型的思考和 tool_calls，**必须原样进历史**）→ `tool`（工具结果） |
| 输入 | 各角色的新消息 |
| 输出 | 不断增长的 messages 数组（发给模型的完整上下文） |
| 着重注意 | ① `assistant` 返回的 tool_calls 消息**必须 append**，否则下次请求报错；② 每条 `tool` 消息靠 `tool_call_id` 与调用一一对应，少一条就 400 错误；③ 顺序敏感：assistant(tool_calls) 后必须紧跟对应 tool 消息，然后才是下一次请求；④ Stage 4 会在这个节点上做"压缩/裁剪"，这里是上下文工程的手术台 |

---

## ✅ 节点 3：工具声明节点（JSON Schema）

**代码**：`common/tools.py` 的 `TOOLS`

| 项 | 内容 |
|---|---|
| 目标 | 让模型"知道有哪些工具可用、何时用、参数怎么填" |
| 作用 | 工具声明是 prompt 的一部分——它直接塑造模型会考虑哪些行动 |
| 怎么做 | 每个 tool 一个 JSON 对象：`name`（动词性命名）、`description`（何时用+边界）、`parameters`（JSON Schema 描述每个参数） |
| 输入 | 无（静态声明） |
| 输出 | 随每次请求发给模型的 tools 数组 |
| 着重注意 | **description 是写给模型看的文档**，标准是"给新同事的入职文档"：说清楚什么时候该用、什么时候不该用。改一个措辞就可能显著改变行为（Stage 3 有故意写烂再修好的实验）。Anthropic 的经验：他们调工具的时间比调主 prompt 还多 |

---

## ✅ 节点 4：工具执行节点（dispatch）

**代码**：`common/tools.py` 的 `REGISTRY` + `dispatch()`

| 项 | 内容 |
|---|---|
| 目标 | 真正去"做"模型要求的事 |
| 作用 | agent 的"手"；agent 和普通聊天机器人的全部区别都来自这个节点真的改变了世界（查了真数据、写了真文件） |
| 怎么做 | 注册表模式：`REGISTRY = {工具名: 函数}`；`dispatch(name, args)` 查表调用。模型给的 `arguments` 是 **JSON 字符串**，必须 `json.loads` 再传参 |
| 输入 | 工具名 + 参数字典（来自模型的 tool_calls） |
| 输出 | 工具结果字符串（建议 JSON 化，方便模型解析） |
| 着重注意 | ① 这是**你自己的确定性代码**，模型碰不到它——所以工具可以放心做权限校验；② **错误信息要"可行动"**：不说 "Error: trace..."，要说"表达式含不支持的字符，请只用数字和 + - * / 重试"——错误消息会进入上下文，是模型自我纠错的依据；③ 结果会进上下文，注意裁剪长度（Stage 4 展开） |

---

## ✅ 节点 5：结果回填节点（tool message）

**代码**：Stage 1/2 脚本中的 `messages.append({"role": "tool", ...})`

| 项 | 内容 |
|---|---|
| 目标 | 把工具执行结果喂回给模型 |
| 作用 | 完成"行动→观察"的闭环；没有这一步，模型点了菜永远等不到上菜 |
| 怎么做 | append 一条 `{"role": "tool", "tool_call_id": <对应id>, "content": <结果字符串>}` |
| 输入 | 工具结果字符串 + 对应的 tool_call_id |
| 输出 | 更新后的 messages |
| 着重注意 | ① `tool_call_id` 必须严格对应，多条 tool_calls 就回填多条 tool 消息；② content 是字符串——不管工具内部返回什么结构，都要序列化；③ 顺序：assistant(tool_calls) → tool → tool → ... → 下一次模型调用 |

---

## ✅ 节点 6：Agent Loop 控制器 ★核心

**代码**：`stages/02_agent_loop/agent_loop.py` 的 `agent_loop()`

| 项 | 内容 |
|---|---|
| 目标 | 让"模型决策→执行→观察"自动循环，直到任务完成 |
| 作用 | **这就是 agent 和 workflow 的分界线**：流程由模型在循环中动态决定，而不是你写死的 |
| 怎么做 | `for step in range(1, max_steps+1)` 循环：① 调模型（节点1）→ ② 无 tool_calls？→ 停止条件 A，返回 content；③ 有 → 逐个 dispatch（节点4）→ ④ 回填（节点5）→ ⑤ 回到①。轮数耗尽走停止条件 B |
| 输入 | 用户问题（自然语言）+ system prompt + 工具声明 |
| 输出 | 最终回答字符串 + 完整 trace |
| 着重注意 | ① Anthropic 循环四步：**收集上下文→行动→验证→重复**，"验证"就是工具返回的真实反馈（grounding）——模型必须每步从环境拿事实，不能靠记忆脑补；② 错误会累积：轮数越多越可能跑偏，所以 max_steps 是安全阀也是质量约束；③ 复杂任务会"涌现"出多轮组合调用（查天气→算差值→回答），你不用写任何流程逻辑——这正是循环的魔力，也埋着失控的种子 |

---

## ✅ 节点 7：停止条件节点

**代码**：`agent_loop()` 内的两个出口

| 项 | 内容 |
|---|---|
| 目标 | 保证循环一定会停 |
| 作用 | 没有停止条件的 agent = 无限烧钱的定时炸弹 |
| 怎么做 | 条件 A：模型返回不含 tool_calls → 它认为做完了（正常出口）；条件 B：达到 `MAX_LOOP_STEPS`（强制出口）。项目里设 20，在 `config.py` |
| 输入 | 每轮模型返回 + 当前步数 |
| 输出 | `stop_reason = "model_done" | "max_steps"`（记进 trace） |
| 着重注意 | ① 强制退出时要**优雅收尾**：汇报已有进展而不是直接崩掉（Stage 8 会完善成"汇报+保存状态"）；② 观察你的任务通常几步收敛——如果一个"简单问题"总跑满 20 步，通常是工具描述或 system prompt 有问题，去读 trace 找原因 |

---

## ✅ 节点 8：Trace 日志节点

**代码**：`common/llm_client.py` 的 `save_trace()`，输出到 `traces/*.json`

| 项 | 内容 |
|---|---|
| 目标 | 完整记录每次运行的决策序列 |
| 作用 | agent 的调试对象不是代码 bug，而是"模型为什么这么决策"——没有 trace 等于盲调；这也是 Stage 9 可观测性的地基 |
| 怎么做 | 运行结束时把完整 messages + stop_reason + token 用量 dump 成 JSON 存盘；`serialize()` 负责 openai 对象→纯 JSON 的转换 |
| 输入 | 运行结束时的 messages |
| 输出 | `traces/月日_时分秒_阶段名.json` |
| 着重注意 | **养成习惯：每次跑完读一遍 trace**，像 PM 看用户行为漏斗一样：哪一步开始偏离？为什么模型选了这个工具？失败的运行（强制停止、答错）比成功的更值得读 |

---

## ✅ 节点 9：真实工具节点（搜索 / 网页抓取）

**代码**：`common/tools.py` 的 `REAL_TOOLS` + `web_search()` / `fetch_url()`

| 项 | 内容 |
|---|---|
| 目标 | 把 mock 工具换成真实世界的"手"，agent 变成真正的研究助理 |
| 作用 | 循环本体一行未改、只换了工具集，agent 的能力就彻底变了——验证"工具是循环里的可替换零件"，也验证"agent 能力上限 = 工具质量" |
| 怎么做 | ① `web_search`：用免费无 key 的 `ddgs` 库（DuckDuckGo），取前 5 条结果；② `fetch_url`：`httpx` 下载 + `trafilatura` 抽正文（去掉导航/广告等噪音） |
| 输入 | `web_search(query)` 搜索词；`fetch_url(url)` 完整链接 |
| 输出 | JSON 字符串：搜索返回 title/url/snippet；抓取返回 url/content（截断后） |
| 着重注意 | ① **工具描述里写了"何时该用/何时不该用"**（摘要够就不必开链接、别重复开同一页）——这是在用 prompt 省钱省轮数；② 每个错误分支都写成"可行动"指引（换关键词重试/换其他链接/仅凭摘要回答），模型收到错误后会照做自我纠错；③ 国内网络若搜索不通，可换 Tavily/Serper/博查，只需改这一个函数 |

## ✅ 节点 10：工具结果裁剪节点

**代码**：`web_search()`/`fetch_url()` 里的 `[:config.MAX_TOOL_RESULT_CHARS]` 等截断逻辑

| 项 | 内容 |
|---|---|
| 目标 | 防止单次工具结果撑爆上下文 |
| 作用 | 工具结果会**原样进入 messages 并在之后每一轮重发**——一个 5 万字的网页如果原样进来，后面每一圈都要为它付 token 钱，还会稀释模型对关键信息的注意力 |
| 怎么做 | 三层裁剪：搜索每条摘要截 300 字符；正文截 `MAX_TOOL_RESULT_CHARS`(4000)；截断时附加说明"已截断，如需更多细节请告诉我" |
| 输入 | 原始工具返回 |
| 输出 | 截断后的高信号字符串 |
| 着重注意 | ① 这是 Stage 4 上下文工程的第一课：**在信息进上下文之前裁剪，比进去之后再删便宜得多**；② 裁剪要保留"信号"（标题/来源/关键句）而不是机械砍半——Anthropic 的原则是"最小的高信号 token 集"；③ 实测对比：Stage 2 一次任务约 1k token，Stage 3 同样 3 步用了约 6k——真实工具的代价一目了然 |

## ✅ 节点 11：上下文工程节点（Compaction + 结构化笔记）

**代码**：`common/context.py`（压缩）+ `common/tools.py` 的 `note_write/note_read`（笔记）+ `stages/04_context/research_agent_v2.py`（集成）

| 项 | 内容 |
|---|---|
| 目标 | 解决"历史每轮全量重发+工具结果超胖"导致的成本暴涨与 context rot |
| 作用 | 没有它，深度调研（15+步）会把上下文滚到几十万 token：每圈全价重付、模型开始"读丢"早期约束。这是 agent 从玩具走向生产的第一道坎 |
| 怎么做 | ① **触发**：每轮调用后看 `usage.prompt_tokens`，超过 `MAX_CONTEXT_TOKENS` 就压缩（用真实计费值判断，比估算准）；② **切分**：旧历史压缩成摘要，保留最近 `COMPACT_KEEP_RECENT` 条原文；切分点不能落在 tool 消息上（会孤儿化，API 400）；③ **摘要提示词**要求保住四类信息：任务目标/关键发现（含来源）/未完成事项/约束；④ **笔记工具**：`note_write` 追加写入 NOTES.md、`note_read` 读回——上下文外的持久记忆 |
| 输入 | 完整 messages + 上一轮的 prompt_tokens |
| 输出 | 压缩后的新 messages（[system]+[user:摘要]+[最近N条原文]）、摘要文本 |
| 着重注意 | 实测踩过的四个坑，每个都是通用教训：① 历史里的 assistant 消息可能是 pydantic 对象，压缩前必须统一转 dict；② **不要人造 assistant 消息**——DeepSeek 思考模式要求每条 assistant 消息带 `reasoning_content`，人造的没有它会被 API 400 拒收；③ **注册表里有 ≠ 模型知道**：笔记工具忘加进 `REAL_TOOLS`（tools 参数）导致模型全程无视它，反复重搜同一问题——模型只认 tools 说明书；④ 压缩发生在调用之后，单轮工具结果本身超阈值时峰值仍会冒头——想控制单轮峰值要靠"进门裁剪"（节点10）配合。另一个重要体感：**没有笔记时，压缩的代价是反复重搜**（发现被压没了，模型只好重新查）——笔记+压缩是配套能力，不是二选一 |

## ✅ 节点 12：结构化输出节点（Plan → Execute → Report）
**代码**：`stages/05_structured_eval/research_agent_v3.py`

| 项 | 内容 |
|---|---|
| 目标 | agent 从"自由发挥"到"按合同交付"：先出 JSON 大纲，再调研，最后交带引用的报告 |
| 作用 | ① 大纲=确定性骨架，照单执行不易漏项（规划是 workflow，调研是 agent——两者混合）；② JSON 输出可被程序解析——agent 之间协作的前提；③ 引用编号让交付物可核验 |
| 怎么做 | ① **Plan**：独立 LLM 调用 + `response_format={"type":"json_object"}`（JSON mode，模型层强制合法 JSON）产出研究大纲；② **Execute**：复用 Stage 2/4 循环，大纲注入 system；③ **Report**：system 要求每个事实带 [n](url) 引用 |
| 输入 | 用户问题 |
| 输出 | 大纲 dict、引用化报告、证据列表（供 judge 用） |
| 着重注意 | **★agent 不会自己收敛**——"永远还差一点"是默认行为（实测两次步数耗尽，模型 endless 调研）。收敛必须由 runtime 管理：剩 2 步时注入死线消息"立即 note_read 并输出报告"。这是确定性代码的职责。另外规划器会贪多（把大纲写成完美主义清单），prompt 要硬性限制子问题数量（≤3个） |

## ✅ 节点 13：评测节点（LLM-as-judge）

**代码**：`evals/questions.json`（题库）+ `evals/judge.py`（评分器）+ `evals/run_eval.py`（运行器）

| 项 | 内容 |
|---|---|
| 目标 | 让每次改动都有分数可对比——agent 的回归测试 |
| 作用 | 没有 eval 的 agent 迭代 = 没有验收标准的 PRD；Anthropic 经验：早期 20 题就能把成功率从 30% 推到 80% |
| 怎么做 | ① 题库：12 道真实研究题（factual/comparison/open 三种题型）；② 运行器逐题跑 agent，收集答案+证据；③ **judge**：一次 LLM 调用按 rubric 打四个子分（supported有据/sourced有源/complete覆盖/concise简洁），JSON mode 输出结构化分数 |
| 输入 | 每题：问题+大纲+证据+答案 |
| 输出 | 0~1 分、四个子分、扣分原因；聚合成 Markdown 报告（evals/results/） |
| 着重注意 | ① **groundedness 是灵魂**：judge 拿到的不是"世界真相"而是 agent 调研时的原始证据，检查"结论是否被证据支撑"——首跑就抓到了疑似编造的价格（Team档$1590/年在证据中无支撑）；② 单 judge 优于多 judge（Anthropic 实测更稳定）；③ judge 也可能误判，分数是信号不是真理——低分题要人工复核答案；④ 题目要小而具体（每题5-12步可完成），从真实需求来 |

---

## ✅ 节点 14：Workflow 管道节点（固定路径的对照面）

**代码**：`stages/06_patterns/patterns.py` 的 `run_workflow()`

| 项 | 内容 |
|---|---|
| 目标 | 亲手实现"流程由代码写死"的对照面——LLM 只在工位上干活，无权决定下一步 |
| 作用 | 它是 agent 的经济学参照物：实测质量 0.5~0.83 波动、成本仅 agent 的 **1/13**（2.1k vs 28k tok）、速度快 1.5~4 倍。**"质量差距小就选它"是产品决策的第一算式** |
| 怎么做 | 四段固定管道：模板拼搜索词（不经过模型）→ 逐个执行 web_search（直接调工具函数）→ 全部证据一次调用写报告 → 结束。无循环、无决策点 |
| 着重注意 | 弱点也实测了：证据质量决定天花板——搜索词是模板拼的，命中差时覆盖度塌方（judge 批"覆盖不足一半"）。**workflow 的质量上限 = 管道设计者的预见力**，它无法应对没预见到的情况 |

## ✅ 节点 15：评审-修订节点（Evaluator-Optimizer）

**代码**：`stages/06_patterns/patterns.py` 的 `run_reflective()`

| 项 | 内容 |
|---|---|
| 目标 | 让输出经历"草稿→批评→修订"的质量回路，可提前退出（评审员放行） |
| 怎么做 | 循环≤2轮：评审调用（JSON输出 pass/issues，要求意见具体可修）→ pass 则退出 → 否则修订调用（拿着意见改稿） |
| 输入/输出 | 问题+证据+草稿 → 修订稿 + 评审日志 |
| 着重注意 | **★首跑翻车，是本节点最大的学习成果**：反思版平均分（0.55-0.58）反而低于纯 workflow（0.57-0.82）。原因：修订者是"纯文本改稿"——评审员说"缺 DeepSeek 定价数字"，它手里没有搜索工具，补不了证据，只能删改文字，甚至把已有内容删没了。**结论：Evaluator-Optimizer 只有在修订者能对意见"采取行动"（如带着问题重新调研）时才有正收益；纯文字修订≈化妆品。** 另外单题结果噪声大（同题两次运行分数差 0.2+），结论必须看多题均值 |

---

## 🚧 后续节点预告（做到对应阶段时，在这里补全文档）

| 节点 | 所属阶段 | 一句话说明 |
|---|---|---|
| N16 Orchestrator-Workers 多智能体节点 | Stage 7 | 主 agent 拆题、子 agent 并行、上下文隔离 |
| N17 记忆节点 | Stage 8 | 跨会话偏好持久化（memory.json） |
| N18 人工确认节点（HITL） | Stage 8 | 用特殊工具调用实现暂停等批准 |
| N19 护栏节点 | Stage 8 | 轮数/费用上限、工具白名单、优雅收尾 |
| N20 可观测性节点 | Stage 9 | 结构化 trace + 失败模式归类复盘 |

---

## 附：当前阶段运行手册

```bash
# 0) 一次性准备
pip3 install openai python-dotenv
cp .env.example .env      # 然后填入三个值

# 1) Stage 0：多轮对话，观察 token 随轮数增长
python3 stages/00_first_chat/chat.py

# 2) Stage 1：单次工具调用链（手动版）
python3 stages/01_tool_call/tool_call.py

# 3) Stage 2：★ 完整 agent loop
python3 stages/02_agent_loop/agent_loop.py

# 4) Stage 3：真实工具的研究助理（需先 .venv/bin/pip install -r requirements.txt 补装 ddgs/trafilatura/httpx）
.venv/bin/python stages/03_real_tools/research_agent.py
```

每次实验后：读 `traces/` 里刚生成的 JSON → 把发现记到 `notes/stage2.md`。

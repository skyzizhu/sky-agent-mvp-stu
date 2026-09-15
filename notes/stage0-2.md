# 联调笔记：Stage 0–2（2026-09-12，模型 deepseek-flash）

## Stage 0：多轮对话
- 实测 token：第 1 轮 input=47，第 2 轮 input=70 —— 第二轮把第一轮的 input(47)+output(54) 里的一部分历史重发了，input 随轮数线性增长。这就是"API 无状态"的直接证据。
- trace: `traces/0912_204705_stage0_chat.json`

## Stage 1：单次工具调用（重要的"失败"案例 ⭐）
- 模型第 1 步调 `get_weather(北京)` 正常；但拿到结果后**没有直接回答**，而是又请求调 `calculator("26-35")` 算差值。
- 手动链路只处理一轮 tool_calls，所以脚本把 calculator 的调用也打印出来了、content 为空。
- **教训（写进 AGENT_NODES 节点 6 的活证据）**：任务需要几轮工具调用是模型根据任务复杂度动态决定的，你无法预知 —— 所以手动串联不可行，必须写循环。
- trace: `traces/0912_204716_stage1_tool_call.json`

## Stage 2：Agent Loop
- 3 步收敛：查天气(26°C) → 算差值(-9) → 未再请求工具 → 停止条件 A（model_done）触发，最终回答带来源标注。
- 第 3 轮 input 已到 669 tok：历史滚雪球，直观预告 Stage 4 上下文工程的必要性。
- trace: `traces/0912_204729_stage2_agent_loop.json`

## 待做
- [ ] Stage 2 的 4 个进阶实验（多城市比较 / max_steps=2 / 删掉 system prompt 约束 / 故障工具自纠错）
- [ ] Stage 3：真实搜索工具替换 mock

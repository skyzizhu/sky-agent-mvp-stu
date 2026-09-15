# Stage 4 复盘：上下文工程（2026-09-15）

## 做了什么
- `common/context.py`：compaction——prompt_tokens 超阈值（4000）时，旧历史压成摘要、保留最近6条原文重建上下文
- `common/tools.py`：note_write / note_read 结构化笔记工具（外置记忆）
- `stages/04_context/research_agent_v2.py`：循环里集成压缩检查+笔记，每轮打印 token 账单

## 实测数据（任务：Coze/Dify/文心 三平台调研，26步完成）
- 总输入 108k tok / 峰值 6.3k / **压缩 13 次** / 笔记 7367 字节
- 对照：无压缩时上下文会单调滚到几十万 token
- 亮点行为：第25步把"未查证到的项"如实写入笔记再作答（诚实汇报）

## 踩坑实录（4个，都是通用教训）
1. **pydantic vs dict**：历史里的 assistant 消息是 pydantic 对象，压缩模块按 dict 处理直接崩 → 统一 `_as_dict()` 规范化
2. **人造消息被拒收**：压缩后拼的"收到，继续"assistant 消息没有 `reasoning_content`，DeepSeek 思考模式 400 → 不要人造 assistant 消息，摘要直接以 user 消息衔接
3. **注册表有 ≠ 模型知道**：note_write 忘加进 REAL_TOOLS（tools 参数），模型全程不知道笔记存在 → 反复重搜同一问题。模型只认 tools 说明书
4. **压缩是事后药**：单轮工具结果本身就能超阈值（一次 fetch 4000字符≈2.5k tok），峰值仍会冒头 → 要配合进门裁剪（节点10）

## 最重要的体感
**没有笔记时，压缩的代价是反复重搜**——发现被压进摘要（有损），模型忘了细节只好重查（第16-20步连搜4次"扣子专业版价格"）。笔记+压缩是配套能力：压缩负责"上下文短"，笔记负责"事实不丢"。

## 待办
- [ ] 实验：调 MAX_CONTEXT_TOKENS / COMPACT_KEEP_RECENT 观察质量-成本权衡
- [ ] Stage 5：结构化输出 + 评测集

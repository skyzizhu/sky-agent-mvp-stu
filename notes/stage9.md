# Stage 9 复盘：可观测性（2026-09-16）

## 建了什么（三层）
1. `common/observability.py`：RunLog——所有运行统一追加 logs/runs.jsonl
   （问题/实现/步数/工具序列/tokens/预算/stop_reason/errors）
2. `classify_failures.py`：LLM把非正常收尾归入固定类目 → logs/failure_report
   （注意提示词教了边界："审批拒绝是正常拦截不算故障"）
3. `dashboard.py`：静态HTML大盘（observatory/index.html）
   ——成功收尾率/累计token/失败分布/运行历史表

## 实测数据（首批入库的3次运行）
- 61581/60000 model_done（Dify定价，正常）
- 43375/60000 model_done（Cursor+HITL拒绝，正常拦截）
- 5415/3000 budget_exhausted → 归类"预算截断" ✓
- 面板：总运行3、正常收尾率67%、失败分布1次预算截断

## 要点
1. **统一入口才能统一观测**的制度化：RunLog就是Stage 6教训的落地——
   绕过统一入口的运行=观测盲区
2. **归类要教边界**：安全拦截（HITL拒绝）≠ 故障，否则误报
3. **面板合格标准三问**：成功率多少？token花在哪？失败归哪几类？
4. 本阶段的根隐喻：Stage 2说"读trace像看漏斗"——现在漏斗有了仪表盘

## 全项目收官状态
节点0-20完成。生产闭环已齐：需求→多方案→评测选型→记忆/HITL/护栏→观测。
Stage 10选修：LangGraph重写对比 + MCP server。

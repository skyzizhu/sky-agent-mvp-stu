# Stage 11 复盘：研究工作台 GUI（2026-09-17）

## 建了什么
- `common/agent_core.py`：可注入内核——emit/approver/stop_check 三个 IO 口
  依赖注入；决策逻辑/工具/提示词/护栏零改动（CLI 回归 + e2e 验证）
- `stages/08_production/production_agent.py`：变成 CLI 薄壳（不注入=原行为）
- `webapp/`：FastAPI SSE 事件流 + 审批/中止端点 + 浅色工作台单页

## 事件流验证（真实运行）
- 8 步研究：step×8（含 note_write/note_read）→ final（23.7k tok，model_done）→ done
- HITL web 流程：send_report → approval_request → 网页批准(POST) →
  approval_result → agent 继续输出 ✓

## 踩坑
1. agent._thread 未回填 → SSE 生成器 AttributeError 断流
   （线程对象存路由字典，没挂回 agent——结构性资源要在创建处挂好）
2. approval_request 双发：agent_core 循环与 web approver 各发一次 → 去重
3. 审批是阻塞设计（线程等 Event 最多600s）——生产换任务队列+轮询

## 架构认知
依赖注入式重构：把循环里写死的 print/input 抽成接口，
注入 CLI 实现=终端版，注入 Web 实现=工作台版。
"一个内核，两种脸"——这是 agent 产品化的标准路径。

## 待办
- [ ] Phase 2：预算滑块、运行详情页（trace 回放）、记忆查看/编辑
- [ ] 审批超时的前端倒计时提示

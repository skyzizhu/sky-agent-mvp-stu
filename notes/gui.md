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

## 层级编号视图（2026-09-17 追加）
- agent_core: _emit_sub 自动编号 step.sub（每步重置）；新增 backfill/inject 颗粒
- 前端: 步分组 <details> + ul.subs 编号列表；compact 颗粒展开含目的/方法/统计/摘要全文
- 验证: 0.1→0.2→1.1→1.2→1.3→1.4→2.1… 序列正确

## 第一批优化（2026-09-17）
1. 搜索源：Tavily 优先 + ddgs 降级（config.TAVILY_API_KEY）
2. 预算硬收尾：95% 拔工具箱（无 tools 调用强制结题），stop_reason=budget_hard_stop
   验证：2967/3000 非空结题报告 ✓（对比此前 4563/3000 且无产出）
3. MCP 生命周期：close()（stop事件→context退出→join→pkill兜底）
   + get_shared_client 共享单例 + atexit 兜底
   验证：同实例 ✓ / close 后线程退出 ✓ / 0 残留进程 ✓

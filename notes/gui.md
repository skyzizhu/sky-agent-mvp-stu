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

## 预算档位（2026-09-17 追加）
- config: BUDGET_TIERS {quick:10k, standard:30k, deep:100k}，DEFAULT=standard
- API: /api/research 接收 tier；/api/config 返回 tiers
- 前端: 三档 chip 选择器，仪表随档位切换
- 验证: quick 档 final.max=10000 ✓
- 依据: 60k 一刀切会腰斩合法深度任务（85-108k），对简单任务又虚高；
  分档 = 把"成本 vs 完整度"权衡交还用户（Anthropic effort scaling 思路）

## 历史详情回放（2026-09-17 追加）
- 事件落盘 logs/events_{run_id}.jsonl（emit 时同步写，与SSE队列同源）
- /api/runs/{id}/events 回放接口；前端 renderEvent 统一渲染器
  （实时 SSE 与历史回放共用，live 参数控制审批按钮态）
- 左栏历史条目点击 → 加载回放 + 选中高亮；旧运行无存档时显示提示
- 验证：新运行 17 事件存档/回放/含 final 报告 ✓；旧运行降级提示 ✓


## 颗粒度与折叠调整（2026-09-17 追加2）
- 档位调整：快问 2 万 / 标准 5 万 / 深度 12 万 token（用户定的数字）
- 步分组默认折叠（点击标题展开）
- step 颗粒详单升级为全量：model_input（发给模型的完整 messages 零删减）
  + model_output（模型返回全字段，含 reasoning_content/tool_calls/usage 明细）
- 工具颗粒：完整输出不再二次截断（源端 MAX_TOOL_RESULT_CHARS 裁剪仍生效）
- 教训：前端脚本反复改，本会话已三次"改A碰坏B"（tier 绑定丢失/msBadge 误删/
  loadHistory 漏调用）——小步提交 + 浏览器实测，缺一不可


## 不限额度档（2026-09-17 追加3）
- 第四档 unlimited：BUDGET_TIERS 里为 None；Budget 支持不限额（水位永不触发）
- 不限 ≠ 失控：步数上限/死线注入/熔断器/中止按钮全部仍然生效
- agent_core：budget_max 用哨兵对象区分"未传"（走默认档）与"显式 None"（不限）
  ——踩坑：__init__ 解析后忘了存回 self.budget_max → AttributeError（已修）
- 前端：第四个 chip「深度+ · 不限额度」；不限档仪表显示已用 token、无百分比
- 教训：测试环境变量（LOW_BUDGET）会覆盖被测配置——验证前先清环境

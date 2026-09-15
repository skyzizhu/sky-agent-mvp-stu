# Stage 7 复盘：多智能体（2026-09-15）

## 建了什么
- `sub_agent.py`：Worker = 精简循环（任务书注入system + 死线护栏 + 独立笔记），
  产出浓缩结论（≤700字要点+来源），无拆题权（防套娃）
- `orchestrator.py`：decompose（JSON拆1~4份任务书）→ ThreadPool并行dispatch
  → collect → synthesize 汇总报告
- `common/tools.py`：笔记路径改 thread-local（并行worker各自独立笔记文件）

## 实测数据（同题：三平台定位+收费对比）
| 指标 | 单agent(v2, Stage4) | 多智能体(Stage7) |
|---|---|---|
| 上下文 | 主进程滚到10万+tok | 主agent只收~1.7k字结论×3 |
| 墙钟 | ~400s（顺序） | 470s（并行加速2.5x，含限流重试） |
| tokens | 108k | 102k（3个worker合计） |

## 首跑翻车实录（3个，全有营养）
1. **三个worker全部步数耗尽不交卷**：Stage 5 病根在子agent复发
   → 修复：死线注入移植（剩2步强制 note_read+输出）
2. **我的bug**：synthesize 返回 message 对象而非 .content 字符串
3. **并行打爆免费搜索**：3 worker同时请求 DuckDuckGo → 超时/限流
   → 修复：搜索重试退避（2s/4s）+ worker错峰启动（stagger 0/5/10s）

## 大结论
- **多智能体 = 用"组织分工"解决单agent的上下文肥胖**，并行顺带提速
- **诚实汇报可以设计进责任链**：子agent标"未查到" → 主agent输出
  "本次调研失败，请勿用于决策"——prompt护栏逐级传导
- **并行会放大工具层的一切弱点**（限流、反爬、登录墙）：
  工具可靠性是多智能体的地板；生产必须换付费搜索源
- 本轮 Coze/文心数据缺失属**数据源问题非架构问题**（Stage4顺序跑同题
  时Coze定价是查到的），已作为已知限制记录

## 待办
- [ ] 搜索源升级 Tavily/博查 后重跑，验证"数据源修复→结果补全"
- [ ] 主agent"追加派工"循环（收作业后判断信息不足→加派）
- [ ] Stage 8：跨会话记忆 + HITL + 护栏

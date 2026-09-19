"""
全局配置：所有阶段共用。
模型接入走 OpenAI 兼容协议（Chat Completions），
DeepSeek / Qwen(DashScope) / GLM / Kimi / OpenAI / Claude(兼容网关) 都能用同一套代码。

接入新模型时只改 .env，不改代码 —— 这就是"配置与逻辑分离"。
"""
import os
from dotenv import load_dotenv

load_dotenv()  # 读取项目根目录的 .env 文件

# 三要素：任何 Chat Completions 兼容服务都由这三项唯一定位
API_KEY = os.getenv("LLM_API_KEY", "")        # 你的 key
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")  # 服务地址
MODEL = os.getenv("LLM_MODEL", "deepseek-chat")  # 模型名，必须支持 tool calling

# Agent 通用安全参数（Stage 8 会扩展成正式护栏，这里先埋点）
MAX_LOOP_STEPS = 20           # agent loop 最大轮数，防失控
MAX_TOOL_RESULT_CHARS = 4000  # 单次工具结果最大字符数，防上下文爆炸

# Stage 4：上下文工程参数
# 阈值设得低是为了让 compaction 在小任务里也能被演示触发；
# 生产环境中应设为模型窗口的 50%~70%（如 128k 窗口设 70k 左右）
MAX_CONTEXT_TOKENS = 4000
COMPACT_KEEP_RECENT = 6


# Stage 11：预算档位（快问/标准/深度）——按任务类型选，而非全局一刀切
BUDGET_TIERS = {"quick": 20000, "standard": 50000, "deep": 120000,
                "unlimited": None}   # None = 不限 token（步数/死线/熔断仍生效）
DEFAULT_BUDGET_TIER = "standard"

# Stage 11：搜索源配置（可选）。配置 TAVILY_API_KEY 后自动用 Tavily 优先，
# 未配置则降级为免费 ddgs（DuckDuckGo，限流/超时较多）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# Stage 11+：邮件发送配置（send_report 危险工具）。
# HOST/USER/AUTH_CODE 三项全配齐才启用真实发送；缺任何一项自动降级为模拟发送
# （返回 sent(mock)，不报错）——MVP 演示与生产行为同一份代码。
SMTP_HOST = os.getenv("SMTP_HOST", "")            # 如 smtp.163.com
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))    # SSL 端口：163/126/yeah.net 均为 465
SMTP_USER = os.getenv("SMTP_USER", "")            # 完整邮箱地址（同时作为发件人）
SMTP_AUTH_CODE = os.getenv("SMTP_AUTH_CODE", "")  # 授权码（不是邮箱登录密码！）

# Stage 10/11：MCP 服务器配置
# 优先级：.env 的 MCP_SERVERS（JSON数组）> 旧开关 MCP_FS=1（默认filesystem）> 不启用
# 每项格式：{"name": "前缀名", "command": "启动命令", "args": [...]}
import json as _json
from pathlib import Path as _Path
_mcp_raw = os.getenv("MCP_SERVERS", "").strip()
if _mcp_raw:
    MCP_SERVERS = _json.loads(_mcp_raw)
elif os.getenv("MCP_FS") == "1":
    MCP_SERVERS = [{"name": "fs", "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem",
                             str(_Path(__file__).resolve().parent)]}]
else:
    MCP_SERVERS = []

# 诊断开关：True 时在强制结题等关键路径 dump 消息结构到 logs/（排查配对类400）
DEBUG_DUMP = os.getenv("DEBUG_DUMP", "").lower() in ("1", "true")

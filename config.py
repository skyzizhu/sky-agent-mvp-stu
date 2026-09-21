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

# Stage 4：上下文工程参数（标准公式：阈值 = 窗口 − 输出预留 − 工具结果预留 − 安全余量）
# 当前模型窗口 1M：1M − 输出 64K − 工具结果 100K − 安全 36K ≈ 800K（窗口的 80%）
# 常规任务（几万至十几万 token）全程不触发压缩；仅超长任务在逼近窗口前由压缩兜底
MAX_CONTEXT_TOKENS = 800000
COMPACT_KEEP_RECENT = 30


# Stage 11：预算档位（快问/标准/深度）——按任务类型选，而非全局一刀切
BUDGET_TIERS = {"quick": 20000, "standard": 50000, "deep": 120000,
                "unlimited": None}   # None = 不限 token（步数/死线/熔断仍生效）
DEFAULT_BUDGET_TIER = "standard"

# Stage 11：搜索源配置（可选）。配置 TAVILY_API_KEY 后自动用 Tavily 优先，
# 未配置则降级为免费 ddgs（DuckDuckGo，限流/超时较多）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

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

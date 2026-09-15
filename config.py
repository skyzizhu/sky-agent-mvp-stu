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

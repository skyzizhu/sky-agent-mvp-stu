#!/usr/bin/env bash
# ============================================================
# 一键启动研究工作台：自动建环境 → 装依赖 → 查配置 → 启动 → 打开浏览器
# 用法: ./start.sh        停止: ./stop.sh
# ============================================================
set -e
cd "$(dirname "$0")"

PORT=7870
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}[启动]${NC} $1"; }
warn() { echo -e "${YELLOW}[提示]${NC} $1"; }
fail() { echo -e "${RED}[失败]${NC} $1"; exit 1; }
mkdir -p logs

# ---------- 1. Python 虚拟环境 ----------
if [ ! -d .venv ]; then
  say "首次运行：创建虚拟环境 .venv"
  python3 -m venv .venv || fail "创建虚拟环境失败（需要 python3.10+，当前：$(python3 -V 2>&1)）"
fi

# ---------- 2. 依赖（已装则秒过） ----------
if ! .venv/bin/python -c "import fastapi, uvicorn, openai, dotenv, httpx, trafilatura" 2>/dev/null; then
  say "安装依赖（requirements.txt，首次约 1-2 分钟）"
  .venv/bin/pip install -q -r requirements.txt || fail "依赖安装失败，请检查网络后重试"
else
  say "依赖已就绪"
fi

# ---------- 3. 配置 .env ----------
if [ ! -f .env ]; then
  cp .env.example .env
  warn "已从 .env.example 生成 .env，请编辑它填入 LLM_API_KEY 后重新运行 ./start.sh"
  fail "缺少模型 API 配置"
fi
if ! grep -q '^LLM_API_KEY=..*' .env; then
  warn ".env 里 LLM_API_KEY 为空，请填入你的模型 API key"
  fail "缺少模型 API 配置"
fi
say "配置检查通过（.env）"

# ---------- 4. JS 渲染抓取的可选依赖（缺了不影响主流程） ----------
if [ ! -d "$HOME/Library/Caches/ms-playwright" ] && [ ! -d "$HOME/.cache/ms-playwright" ]; then
  warn "未安装 Playwright 浏览器：fetch_js（JS 渲染抓取）不可用。如需启用：.venv/bin/playwright install chromium"
fi

# ---------- 5. 已在运行则直接打开页面 ----------
if lsof -ti :$PORT >/dev/null 2>&1; then
  say "工作台已在运行, 端口 $PORT"
  open "http://127.0.0.1:$PORT/" 2>/dev/null || xdg-open "http://127.0.0.1:$PORT/" 2>/dev/null || true
  echo -e "${GREEN}地址: http://127.0.0.1:$PORT/${NC} ｜ 停止: ./stop.sh"
  exit 0
fi

# ---------- 6. 启动 ----------
say "启动研究工作台, 端口 $PORT ..."
nohup .venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port $PORT > logs/workbench.log 2>&1 &
echo $! > logs/workbench.pid

# ---------- 7. 健康检查（最多 10 秒） ----------
for _ in $(seq 1 20); do
  sleep 0.5
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/config"; then
    say "启动成功，正在打开浏览器..."
    open "http://127.0.0.1:$PORT/" 2>/dev/null || xdg-open "http://127.0.0.1:$PORT/" 2>/dev/null || true
    echo ""
    echo -e "${GREEN}✓ 研究工作台: http://127.0.0.1:$PORT/${NC}"
    echo -e "  日志: logs/workbench.log   ｜   停止: ./stop.sh"
    exit 0
  fi
done
fail "启动超时，请查看 logs/workbench.log 排查"

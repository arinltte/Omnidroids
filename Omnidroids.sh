#!/bin/bash
# =====================================================================
#  Omnidroids 一键启动器（macOS，双击运行 / double-click to run）
#
#  单进程架构（§10）：后端(Flask :5001) 直接托管构建后的 Vue SPA，
#  运行时无 Node/Vite 开发服务器。启动后只打开主页面。
#
#  提示：
#    - Finder 提示“无法打开”时：右键 -> 打开；或先执行一次:
#        chmod +x Omnidroids.sh
#    - 不自动开浏览器: OPEN_BROWSER=0 ./Omnidroids.sh
#    - 停止: 在本窗口按 Ctrl+C（仅发送 SIGTERM —— 绝不 kill -9，
#      Kuzu WAL 在 SIGBUS 后会损坏；后端内部还会自行清理）。
#    - 所有 HuggingFace 产物都导出到 Omnidroids/.cache/hf，绝不落入 ~/.cache。
# =====================================================================
set -u

# --- 定位项目目录 ---
SCRIPT_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" 2>/dev/null && pwd)"
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/backend" ] && [ -d "$SCRIPT_DIR/frontend" ]; then
  PROJECT_DIR="$SCRIPT_DIR"
else
  PROJECT_DIR="/Users/chenjinshen/Downloads/Prediction/Omnidroids"
fi
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
LOGDIR="$PROJECT_DIR/logs"; mkdir -p "$LOGDIR"

# 确保能找到 brew 安装的 uv/node/npm
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
OPEN_BROWSER="${OPEN_BROWSER:-1}"

info(){ printf '\033[1;36m[omnidroids]\033[0m %s\n' "$*"; }
ok(){   printf '\033[1;32m[  ok  ]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[ warn ]\033[0m %s\n' "$*"; }
err(){  printf '\033[1;31m[ fail ]\033[0m %s\n' "$*"; }

port_busy(){ lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
http_up(){ curl -s --max-time 3 "$1" >/dev/null 2>&1; }

BACKEND_PID=""
cleanup(){
  echo ""
  info "正在停止本脚本启动的后端 (SIGTERM)…"
  if [ -n "$BACKEND_PID" ]; then
    kill -TERM "$BACKEND_PID" 2>/dev/null
    # 等待优雅退出（Kuzu 关闭 WAL）；只有我们自己启动的进程才处理
    for _ in $(seq 1 30); do
      kill -0 "$BACKEND_PID" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$BACKEND_PID" 2>/dev/null; then
      warn "后端 15 秒后仍在运行；不使用 kill -9（会损坏 Kuzu WAL）——请手动检查"
    fi
  fi
  rm -f "$LOGDIR/backend.pid"
  ok "已停止。日志见 $LOGDIR"
}
trap 'cleanup; exit 0' INT TERM

echo "=============================================================="
info "项目目录: $PROJECT_DIR"

# ---------------- 1) 依赖（缺失才安装）----------------
info "1/4 依赖检查（缺失才安装）…"
if [ ! -d "$BACKEND_DIR/.venv" ]; then
  info "安装后端依赖 (uv sync)…"
  ( cd "$BACKEND_DIR" && uv sync ) || err "后端依赖安装失败"
fi
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  info "安装前端依赖 (npm install)…"
  ( cd "$FRONTEND_DIR" && npm install ) || err "前端依赖安装失败"
fi

# ---------------- 2) 构建 SPA（仅当缺失；用户拿到的是已构建包）----------------
info "2/4 检查 SPA 构建产物 (frontend/dist)…"
if [ ! -f "$FRONTEND_DIR/dist/index.html" ]; then
  info "frontend/dist 缺失 —— 构建一次 (npm run build)…"
  ( cd "$FRONTEND_DIR" && npm run build ) || err "前端构建失败"
else
  ok "frontend/dist 已存在"
fi

# ---------------- 3) 环境：所有 HF 产物留在 Omnidroids 文件夹内 ----------------
export HF_HOME="$PROJECT_DIR/.cache/hf"
info "HF_HOME=$HF_HOME"

# ---------------- 4) 启动后端（单进程，同时服务 API + SPA）----------------
info "3/4 启动后端(:5001, 托管 SPA)…"
if port_busy 5001; then
  warn ":5001 已被占用，跳过启动（假设已在运行）。"
else
  ( cd "$BACKEND_DIR" && exec uv run python run.py ) > "$LOGDIR/backend.log" 2>&1 &
  BACKEND_PID=$!
  echo "$BACKEND_PID" > "$LOGDIR/backend.pid"
  info "后端 PID=$BACKEND_PID  日志: logs/backend.log"
fi

info "等待后端就绪…"
READY=0
for _ in $(seq 1 60); do
  if http_up "http://localhost:5001/health"; then READY=1; break; fi
  sleep 1
done

echo "=============================================================="
if [ "$READY" = "1" ]; then
  ok "Omnidroids 已启动！"
else
  err "后端未就绪 —— 请查看 logs/backend.log"
fi
echo "   操作界面(主页面) : http://localhost:5001"
echo "   后端 API       : http://localhost:5001/api"
echo "   本地 AI 监控    : http://localhost:5001/monitor (手动访问；第3步模拟页也有入口)"
echo "   停止: 在本窗口按 Ctrl+C (SIGTERM，绝不 kill -9)"
echo "=============================================================="

if [ "$OPEN_BROWSER" = "1" ]; then
  open "http://localhost:5001" 2>/dev/null || true
fi

# 保持前台，等待被 Ctrl+C（cleanup 里 SIGTERM 停止后端）
wait

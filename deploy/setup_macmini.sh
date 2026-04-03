#!/bin/bash
# ============================================================
#  AI Sticker Workbench — Mac Mini 一键部署脚本 (含内网穿透)
#  使用方式:
#    1. 把整个项目传到 Mac Mini
#    2. cd 到项目目录
#    3. chmod +x deploy/setup_macmini.sh
#    4. ./deploy/setup_macmini.sh
# ============================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "============================================"
echo "  AI Sticker Workbench — Mac Mini 部署"
echo "  (含 Cloudflare Tunnel 内网穿透)"
echo "============================================"
echo "项目目录: $PROJECT_DIR"
echo ""

# ── 1. 检查 & 安装基础工具 ──────────────────────
echo "[1/7] 检查基础环境..."

if ! command -v brew &>/dev/null; then
    echo "  ✗ 未找到 Homebrew，请先安装:"
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    exit 1
fi
echo "  ✓ Homebrew 已就绪"

if command -v python3 &>/dev/null; then
    PY=$(command -v python3)
    PY_VER=$($PY --version 2>&1)
    echo "  ✓ 找到 $PY_VER"
else
    echo "  安装 Python..."
    brew install python@3.12
    PY=$(command -v python3)
fi

if command -v cloudflared &>/dev/null; then
    echo "  ✓ cloudflared 已安装"
else
    echo "  安装 cloudflared (内网穿透)..."
    brew install cloudflared
    echo "  ✓ cloudflared 安装完成"
fi

# ── 2. 创建虚拟环境 ─────────────────────────────
echo ""
echo "[2/7] 创建虚拟环境..."
if [ ! -d "venv" ]; then
    $PY -m venv venv
    echo "  ✓ 虚拟环境已创建"
else
    echo "  ✓ 虚拟环境已存在，跳过"
fi
source venv/bin/activate

# ── 3. 安装依赖 ─────────────────────────────────
echo ""
echo "[3/7] 安装 Python 依赖..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  ✓ 依赖安装完成"

# ── 4. 创建 .env ────────────────────────────────
echo ""
echo "[4/7] 配置环境变量..."

LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1")
echo "  检测到本机 IP: $LOCAL_IP"

if [ ! -f ".env" ]; then
    SESSION_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > .env << ENVEOF
# ============================================================
# AI Sticker Workbench — 生产环境配置
# ============================================================

# ---- OpenAI API ----
OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>
OPENAI_BASE_URL=<YOUR_OPENAI_BASE_URL>
OPENAI_MODEL=gpt-5.4

# ---- Anthropic Claude API ----
ANTHROPIC_API_KEY=<YOUR_ANTHROPIC_API_KEY>
ANTHROPIC_BASE_URL=<YOUR_ANTHROPIC_BASE_URL>
CLAUDE_MODEL=claude-sonnet-4-6

# ---- Google Gemini API (图片生成) ----
IMAGE_API_KEY=<YOUR_GEMINI_API_KEY>
IMAGE_BASE_URL=https://generativelanguage.googleapis.com
IMAGE_MODEL=gemini-3.1-flash-image-preview
GEMINI_TEXT_MODEL=gemini-3.1-pro-preview
GEMINI_MAX_CONCURRENCY=6

# ---- Shopify Admin API ----
SHOPIFY_STORE_DOMAIN=<YOUR_STORE>.myshopify.com
SHOPIFY_CLIENT_ID=<YOUR_SHOPIFY_CLIENT_ID>
SHOPIFY_CLIENT_SECRET=<YOUR_SHOPIFY_CLIENT_SECRET>
SHOPIFY_BLOG_HANDLE=blog
SHOPIFY_API_VERSION=2024-01

# ---- Feishu (Sticker Bot) ----
FEISHU_APP_ID=<YOUR_FEISHU_APP_ID>
FEISHU_APP_SECRET=<YOUR_FEISHU_APP_SECRET>
FEISHU_VERIFICATION_TOKEN=<YOUR_FEISHU_VERIFICATION_TOKEN>

# ---- Feishu (Blog Agent Bot) ----
FEISHU_BLOG_APP_ID=<YOUR_FEISHU_BLOG_APP_ID>
FEISHU_BLOG_APP_SECRET=<YOUR_FEISHU_BLOG_APP_SECRET>

# ---- Feishu 多维表格 ----
FEISHU_BITABLE_APP_TOKEN=<YOUR_BITABLE_APP_TOKEN>
FEISHU_BITABLE_URL=<YOUR_BITABLE_URL>
FEISHU_TABLE_NEWS=<YOUR_TABLE_NEWS>
FEISHU_TABLE_TK=<YOUR_TABLE_TK>
FEISHU_TABLE_HISTORY=<YOUR_TABLE_HISTORY>

# ---- Feishu 群 Webhook (可选) ----
FEISHU_WEBHOOK_URL=

# ---- Feishu H5 工作台 (由 start.sh 自动更新为穿透地址) ----
FEISHU_H5_APP_ID=<YOUR_FEISHU_H5_APP_ID>
FEISHU_H5_APP_SECRET=<YOUR_FEISHU_H5_APP_SECRET>
FEISHU_H5_BASE_URL=http://${LOCAL_IP}:8888
FEISHU_H5_REDIRECT_URI=http://${LOCAL_IP}:8888/auth/feishu/callback
FEISHU_H5_SESSION_SECRET=${SESSION_KEY}
FEISHU_H5_AUTO_DEV=true

# ---- Workbench Data Sources ----
TREND_OUTPUT_DIR=trend_fetcher/output
TIKTOK_DB_PATH=data/tiktok_trends.db

# ---- NewsAPI ----
NEWS_API_KEY=<YOUR_NEWS_API_KEY>

# ---- Session / Security (已自动生成) ----
SESSION_SECRET=${SESSION_KEY}

# ---- 服务配置 ----
HOST=0.0.0.0
PORT=8888
ENVEOF
    echo "  ✓ .env 已创建，所有 API Key 已预填"
else
    echo "  ✓ .env 已存在，跳过"
fi

# ── 5. 创建必要目录 ─────────────────────────────
echo ""
echo "[5/7] 创建数据目录..."
mkdir -p data output/h5_jobs output/blogs/images logs
echo "  ✓ 目录已就绪"

# ── 6. 创建启动/管理脚本 ────────────────────────
echo ""
echo "[6/7] 生成管理脚本..."

# ---- start.sh: 先穿透 → 更新 .env → 再启动 Web ----
cat > start.sh << 'STARTEOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
mkdir -p logs

# 检查是否已经在运行
if [ -f .server.pid ] && kill -0 "$(cat .server.pid)" 2>/dev/null; then
    echo "⚠ 服务已在运行 (PID: $(cat .server.pid))，请先执行 ./stop.sh"
    exit 1
fi

echo "=========================================="
echo "  启动 AI Sticker Workbench"
echo "=========================================="

# 1) 先启动 Cloudflare Tunnel（拿到公网地址后再启动 Web）
echo "[1/3] 启动内网穿透..."
nohup cloudflared tunnel --url http://localhost:8888 > logs/tunnel.log 2>&1 &
echo $! > .tunnel.pid
echo "  等待穿透地址生成..."

TUNNEL_URL=""
for i in $(seq 1 15); do
    TUNNEL_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log 2>/dev/null | head -1)
    if [ -n "$TUNNEL_URL" ]; then
        break
    fi
    sleep 2
done

if [ -n "$TUNNEL_URL" ]; then
    echo "  ✓ 穿透地址: $TUNNEL_URL"
    echo "$TUNNEL_URL" > .tunnel_url
else
    echo "  ⚠ 穿透地址获取超时，将使用局域网地址"
    TUNNEL_URL="http://$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo localhost):8888"
fi

# 2) 更新 .env（Web 服务启动前写入，确保读到正确地址）
echo "[2/3] 更新 .env 配置..."
if [ -f .env ]; then
    sed -i '' "s|^FEISHU_H5_BASE_URL=.*|FEISHU_H5_BASE_URL=${TUNNEL_URL}|" .env
    sed -i '' "s|^FEISHU_H5_REDIRECT_URI=.*|FEISHU_H5_REDIRECT_URI=${TUNNEL_URL}/auth/feishu/callback|" .env
    echo "  ✓ .env 已更新为: $TUNNEL_URL"
fi

# 3) 启动 Web 服务（此时 .env 已包含正确的穿透地址）
echo "[3/3] 启动 Web 服务..."
nohup python web_app.py > logs/server.log 2>&1 &
echo $! > .server.pid
sleep 3

if kill -0 "$(cat .server.pid)" 2>/dev/null; then
    echo "  ✓ Web 服务已启动 (PID: $(cat .server.pid))"
else
    echo "  ✗ Web 服务启动失败，查看日志:"
    tail -10 logs/server.log
    exit 1
fi

echo ""
echo "=========================================="
echo "  启动成功！"
echo "=========================================="
echo ""
echo "  公网地址: $TUNNEL_URL"
echo "  局域网:   http://$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo localhost):8888"
echo ""
echo "  飞书配置 (复制以下地址):"
echo "    桌面端主页:  $TUNNEL_URL"
echo "    移动端主页:  $TUNNEL_URL"
echo "    重定向 URL: ${TUNNEL_URL}/auth/feishu/callback"
echo "    可信域名:   $(echo $TUNNEL_URL | sed 's|https://||')"
echo ""
echo "  日志查看:"
echo "    tail -f logs/server.log   (Web 服务)"
echo "    tail -f logs/tunnel.log   (穿透隧道)"
echo ""
STARTEOF

# ---- stop.sh: 停止服务 + 穿透 ----
cat > stop.sh << 'STOPEOF'
#!/bin/bash
cd "$(dirname "$0")"

echo "停止服务..."

# 停止穿透
if [ -f .tunnel.pid ]; then
    PID=$(cat .tunnel.pid)
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "  ✓ 穿透隧道已停止 (PID: $PID)"
    fi
    rm -f .tunnel.pid .tunnel_url
else
    pkill -f "cloudflared tunnel" 2>/dev/null && echo "  ✓ 穿透隧道已停止"
fi

# 停止 Web 服务
if [ -f .server.pid ]; then
    PID=$(cat .server.pid)
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "  ✓ Web 服务已停止 (PID: $PID)"
    fi
    rm -f .server.pid
else
    pkill -f "python web_app.py" 2>/dev/null && echo "  ✓ Web 服务已停止"
fi

echo "  全部已停止"
STOPEOF

# ---- restart.sh ----
cat > restart.sh << 'RESTARTEOF'
#!/bin/bash
cd "$(dirname "$0")"
echo "重启服务..."
bash stop.sh
sleep 2
bash start.sh
RESTARTEOF

# ---- status.sh ----
cat > status.sh << 'STATUSEOF'
#!/bin/bash
cd "$(dirname "$0")"

echo "=========================================="
echo "  服务状态"
echo "=========================================="

# Web 服务
if [ -f .server.pid ] && kill -0 "$(cat .server.pid)" 2>/dev/null; then
    echo "  Web 服务:   ✓ 运行中 (PID: $(cat .server.pid))"
else
    echo "  Web 服务:   ✗ 未运行"
fi

# 穿透隧道
if [ -f .tunnel.pid ] && kill -0 "$(cat .tunnel.pid)" 2>/dev/null; then
    echo "  穿透隧道:   ✓ 运行中 (PID: $(cat .tunnel.pid))"
    if [ -f .tunnel_url ]; then
        echo "  公网地址:   $(cat .tunnel_url)"
    fi
else
    echo "  穿透隧道:   ✗ 未运行"
fi

# 地址
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "localhost")
echo "  局域网地址: http://$IP:8888"
echo ""
STATUSEOF

chmod +x start.sh stop.sh restart.sh status.sh

echo "  ✓ 管理脚本已生成"

# ── 7. 完成 ─────────────────────────────────────
echo ""
echo "[7/7] 安装 cloudflared..."
if command -v cloudflared &>/dev/null; then
    echo "  ✓ cloudflared 已就绪 ($(cloudflared --version 2>&1 | head -1))"
else
    echo "  正在安装..."
    brew install cloudflared
fi

echo ""
echo "============================================"
echo "  部署完成！"
echo "============================================"
echo ""
echo "  一键启动 (含内网穿透):"
echo "    ./start.sh"
echo ""
echo "  启动后会自动:"
echo "    1. 启动 Web 服务 (端口 8888)"
echo "    2. 启动 Cloudflare 穿透隧道"
echo "    3. 输出公网地址 (用于飞书配置)"
echo "    4. 自动更新 .env 中的飞书地址"
echo ""
echo "  管理命令:"
echo "    ./stop.sh      停止所有"
echo "    ./restart.sh    重启所有"
echo "    ./status.sh     查看状态和地址"
echo ""
echo "  ⚠️  注意: 每次 restart 穿透地址会变化"
echo "     飞书开放平台需要同步更新重定向URL"
echo ""

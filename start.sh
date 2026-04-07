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

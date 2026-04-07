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

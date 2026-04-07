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

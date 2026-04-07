#!/bin/bash
cd "$(dirname "$0")"
echo "重启服务..."
bash stop.sh
sleep 2
bash start.sh

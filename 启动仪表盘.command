#!/bin/bash
# 直播间仪表盘一键启动脚本（Tailscale 安全版）
# 双击此文件即可启动

cd /Users/anirv/Downloads/xinlaoke

echo "🔄 停止旧的 Streamlit 进程..."
pkill -f "streamlit run" 2>/dev/null
sleep 2

echo "🚀 启动 Streamlit..."
STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
STREAMLIT_SERVER_HEADLESS=true \
nohup /Users/anirv/Library/Python/3.9/bin/streamlit run app.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  --server.headless true \
  < /dev/null > /tmp/streamlit.log 2>&1 &

echo "⏳ 等待启动..."
sleep 6

# 获取 Tailscale IP
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || /Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4 2>/dev/null)

echo ""
echo "✅ 启动成功！"
echo "========================================"
if [ -n "$TAILSCALE_IP" ]; then
    echo "🔒 Tailscale 私有链接: http://$TAILSCALE_IP:8501"
    echo "   （只有加入你 Tailscale 网络的人才能访问）"
else
    echo "⚠️  Tailscale 未连接，请先打开 Tailscale app"
fi
echo "💻 本地链接:          http://localhost:8501"
echo "========================================"
echo ""
echo "📤 把 Tailscale 私有链接发给同事（同事需先加入你的 Tailscale 网络）"
echo "❌ 关闭此窗口会停止服务，请保持此窗口开着"
echo ""

# 自动打开本地浏览器
open "http://localhost:8501"

# 保持窗口不关闭
read -p "按回车键退出（退出后服务停止）..."

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

# 获取局域网 IP（优先 172.16.x.x 公司内网，其次任何 inet 地址）
LAN_IPS=$(ifconfig | awk '/inet /{print $2}' | grep -v 127.0.0.1)
COMPANY_IP=$(echo "$LAN_IPS" | grep '^172\.16\.' | head -1)
OTHER_IPS=$(echo "$LAN_IPS" | grep -v '^172\.16\.')

echo ""
echo "✅ 启动成功！"
echo "========================================"
echo "💻 本地链接:          http://localhost:8501"
if [ -n "$COMPANY_IP" ]; then
    echo "🏢 公司内网链接:      http://$COMPANY_IP:8501"
fi
for ip in $OTHER_IPS; do
    echo "🌐 局域网链接:        http://$ip:8501"
done
if [ -n "$TAILSCALE_IP" ]; then
    echo "🔒 Tailscale 私有:    http://$TAILSCALE_IP:8501"
fi
echo "========================================"
echo ""
echo "📤 同事访问方式："
echo "   • 同公司内网 → 公司内网链接（无需任何账号）"
echo "   • 远程       → Tailscale 私有链接（需加入 Tailscale 网络）"
echo "❌ 关闭此窗口会停止服务，请保持此窗口开着"
echo ""

# 自动打开本地浏览器
open "http://localhost:8501"

# 保持窗口不关闭
read -p "按回车键退出（退出后服务停止）..."

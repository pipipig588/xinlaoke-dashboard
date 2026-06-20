#!/bin/bash
# 直播间仪表盘一键启动脚本（含公网 ngrok 隧道，带密码）
# 双击此文件即可启动

cd /Users/anirv/Downloads/xinlaoke

# ===== 网页访问密码（如需修改，改这一行即可；空字符串=不要密码）=====
DASH_PASS="xinlaoke2026"
# ===================================================================
export DASH_PASSWORD="$DASH_PASS"

# 定位可执行文件（双击启动时 PATH 可能不全，用绝对路径兜底）
NGROK_BIN=$(command -v ngrok || echo /opt/homebrew/bin/ngrok)
STREAMLIT_BIN=/Users/anirv/Library/Python/3.9/bin/streamlit

echo "🔄 停止旧进程..."
pkill -f "streamlit run" 2>/dev/null
pkill -f "ngrok http"   2>/dev/null
sleep 2

echo "🚀 启动 Streamlit..."
STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
STREAMLIT_SERVER_HEADLESS=true \
DASH_PASSWORD="$DASH_PASS" \
nohup "$STREAMLIT_BIN" run app.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  --server.headless true \
  < /dev/null > /tmp/streamlit.log 2>&1 &

echo "⏳ 等待 Streamlit 启动..."
sleep 6

# ===== 启动公网隧道（ngrok；密码已在网页里，隧道不再单独加密码）=====
PUBLIC_URL=""
if [ -x "$NGROK_BIN" ]; then
    echo "🌐 启动公网隧道 (ngrok)..."
    nohup "$NGROK_BIN" http 8501 \
      --log=stdout < /dev/null > /tmp/ngrok.log 2>&1 &

    # 轮询本地 API 拿公网网址（最多等 ~15 秒）
    for i in $(seq 1 15); do
        PUBLIC_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
          | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])
except Exception:
    pass" 2>/dev/null)
        [ -n "$PUBLIC_URL" ] && break
        sleep 1
    done
else
    echo "⚠️  未找到 ngrok，跳过公网隧道（局域网/Tailscale 仍可用）"
fi

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
if [ -n "$PUBLIC_URL" ]; then
    echo "----------------------------------------"
    echo "🌍 公网链接(任何人可访问):"
    echo "     $PUBLIC_URL"
elif [ -x "$NGROK_BIN" ]; then
    echo "----------------------------------------"
    echo "⚠️  公网隧道未取到网址，看 /tmp/ngrok.log 排查"
fi
echo "----------------------------------------"
if [ -n "$DASH_PASS" ]; then
    echo "🔑 网页访问密码: $DASH_PASS （所有链接打开后都要先输）"
else
    echo "🔑 未设置网页密码（任何能打开链接的人都能直接访问）"
fi
echo "========================================"
echo ""
echo "📤 同事访问方式："
echo "   • 同公司内网 → 公司内网链接"
echo "   • 远程       → Tailscale 私有链接（需加入 Tailscale）"
if [ -n "$PUBLIC_URL" ]; then
    echo "   • 任意外网   → 公网链接（无需安装任何东西，打开后输密码即可）"
fi
echo "❌ 关闭此窗口会停止服务，请保持此窗口开着"
echo ""

# 把「公网链接 + 密码」复制到剪贴板，并弹 Mac 通知（方便直接粘贴发给同事）
if [ -n "$PUBLIC_URL" ]; then
    if [ -n "$DASH_PASS" ]; then
        CLIP="直播间销售仪表盘
链接：$PUBLIC_URL
密码：$DASH_PASS"
    else
        CLIP="直播间销售仪表盘
链接：$PUBLIC_URL"
    fi
    printf '%s' "$CLIP" | pbcopy 2>/dev/null \
        && echo "📋 公网链接+密码已复制到剪贴板，可直接粘贴发给同事" \
        && osascript -e 'display notification "公网链接+密码已复制到剪贴板，可直接粘贴发给同事" with title "仪表盘已启动" sound name "Glass"' >/dev/null 2>&1
fi

# 自动打开本地浏览器
open "http://localhost:8501"

# 退出时一并关闭后台进程
cleanup() {
    echo ""
    echo "🛑 正在停止服务..."
    pkill -f "streamlit run" 2>/dev/null
    pkill -f "ngrok http"   2>/dev/null
}
trap cleanup EXIT

# 保持窗口不关闭
read -p "按回车键退出（退出后服务与隧道都会停止）..."

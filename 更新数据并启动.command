#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 换了 data/raw 里的 Excel 之后，双击这个文件：
#   ① 重新跑 preprocess.py 生成最新数据（覆盖 data/processed）
#   ② 成功后自动启动仪表盘（调用 启动仪表盘.command）
#
# 数据没变、只想开仪表盘 → 直接双击「启动仪表盘.command」即可，
# 不用重跑预处理（preprocess 读大 Excel 要几分钟，没必要每次都跑）。
# ═══════════════════════════════════════════════════════════════

cd /Users/anirv/Downloads/xinlaoke || { echo "❌ 找不到项目目录"; read -p "回车退出..."; exit 1; }

# 用 python3（本机没有 python，只有 python3）
PYTHON=$(command -v python3 || echo /usr/bin/python3)

echo "════════════════════════════════════════"
echo "🔄 第 1 步：重新生成数据（preprocess.py）"
echo "   源文件目录：data/raw/"
echo "   读取大 Excel，可能要几分钟，请耐心等待..."
echo "════════════════════════════════════════"
echo ""

# -u 关闭缓冲，实时看到进度
"$PYTHON" -u preprocess.py
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo ""
    echo "════════════════════════════════════════"
    echo "❌ 预处理失败！请看上面的报错信息。"
    echo "   数据未更新，已停止，不启动仪表盘。"
    echo "   常见原因：data/raw 里 Excel 列名变了 / 文件被占用 / 文件损坏。"
    echo "════════════════════════════════════════"
    read -p "按回车键退出..."
    exit 1
fi

echo ""
echo "✅ 数据已更新成功，正在启动仪表盘..."
echo ""

# 接力给现有的启动脚本（它负责起 Streamlit + ngrok + 打开浏览器）
exec bash "启动仪表盘.command"

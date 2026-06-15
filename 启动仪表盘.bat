@echo off
chcp 65001 >nul
REM 直播间销售分析仪表盘 - Windows 一键启动脚本
REM 双击此文件即可启动；如失败请按 README.md 排查。

cd /d "%~dp0"

echo ========================================
echo  直播间销售分析仪表盘 - 启动中
echo ========================================
echo.

REM 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 Python
    echo.
    if exist "python-3.12.9-amd64.exe" (
        echo 已为你准备好 Python 3.12.9 安装包，正在打开...
        echo.
        echo ★★★ 安装时请务必勾选最下面那个 "Add python.exe to PATH" ★★★
        echo.
        echo 安装完成后请重新双击本 .bat 文件启动仪表盘。
        start "" "python-3.12.9-amd64.exe"
    ) else (
        echo 请到 https://www.python.org/downloads/windows/ 下载 Python 3.9+
        echo 安装时务必勾选 "Add Python to PATH"
    )
    pause
    exit /b 1
)

REM 检查依赖
python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo [提示] 首次启动，正在安装依赖（约 1-3 分钟）...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络
        pause
        exit /b 1
    )
)

REM 检查 parquet 数据是否存在
if not exist "data\processed\orders.parquet" (
    echo [提示] 未发现预处理数据，开始处理 data\raw\ 下的 Excel...
    echo        如果 raw 目录是空的，请先把"报表订单.xlsx"放进去再双击本文件。
    echo.
    python preprocess.py
    if errorlevel 1 (
        echo [错误] 预处理失败
        pause
        exit /b 1
    )
)

REM 杀掉旧的 streamlit 进程
echo 停止旧的 Streamlit 进程...
taskkill /F /IM streamlit.exe >nul 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8501 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>nul
timeout /t 2 /nobreak >nul

REM 获取本机 IPv4 地址（取第一个非回环地址）
set "LAN_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    if not defined LAN_IP (
        set "LAN_IP=%%a"
    )
)
set "LAN_IP=%LAN_IP: =%"

echo.
echo ========================================
echo  启动成功！
echo ========================================
echo  本机浏览器:   http://localhost:8501
if defined LAN_IP echo  内网链接:     http://%LAN_IP%:8501
echo ========================================
echo.
echo  - 把内网链接发给同事，同公司网络的人即可访问
echo  - 关闭此窗口会停止服务，请保持窗口开着
echo.

REM 启动 streamlit（前台运行，方便看日志）
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
set "STREAMLIT_SERVER_HEADLESS=true"
start "" "http://localhost:8501"
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.enableCORS false --server.enableXsrfProtection false --server.headless true

pause

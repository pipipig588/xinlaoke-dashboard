@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
REM 直播间销售分析仪表盘 - Windows 一键启动脚本（含 cloudflared 公网隧道）
REM 双击此文件即可启动；如失败请按 README.md 排查。

cd /d "%~dp0"

REM ===== 网页访问密码（如需修改，改这一行即可；留空=不要密码）=====
set "DASH_PASS=xinlaoke2026"
REM ================================================================
set "DASH_PASSWORD=%DASH_PASS%"

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

REM 杀掉旧进程
echo 停止旧进程...
taskkill /F /IM cloudflared.exe >nul 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8501 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>nul
timeout /t 2 /nobreak >nul

REM 准备 cloudflared.exe（一般已随项目附带；缺失则尝试下载）
if not exist "cloudflared.exe" (
    echo [提示] 未发现 cloudflared.exe，尝试下载（需要能访问 GitHub）...
    where curl >nul 2>nul
    if not errorlevel 1 (
        curl -L -o cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
    ) else (
        powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
    )
)

REM 启动 Streamlit（独立最小化窗口，DASH_PASSWORD 已通过环境变量传入）
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
set "STREAMLIT_SERVER_HEADLESS=true"
echo 启动 Streamlit...
start "Dashboard-Streamlit" /min cmd /c python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.enableCORS false --server.enableXsrfProtection false --server.headless true

echo 等待 Streamlit 启动...
timeout /t 7 /nobreak >nul

REM 启动公网隧道（cloudflared 快速隧道，无需账号），日志写入 cloudflared.log
set "PUBLIC_URL="
if exist "cloudflared.exe" (
    echo 启动公网隧道 (cloudflared)...
    del /q cloudflared.log >nul 2>nul
    start "Cloudflared" /min cmd /c cloudflared.exe tunnel --no-autoupdate --url http://localhost:8501 ^> cloudflared.log 2^>^&1

    REM 轮询日志取公网网址（最多约 25 秒）
    set /a _tries=0
    :waiturl
    for /f "usebackq tokens=*" %%u in (`findstr /c:"trycloudflare.com" cloudflared.log 2^>nul`) do (
        for %%t in (%%u) do (
            echo %%t| findstr /b "https://" >nul && set "PUBLIC_URL=%%t"
        )
    )
    if defined PUBLIC_URL goto goturl
    set /a _tries+=1
    if !_tries! geq 25 goto goturl
    timeout /t 1 /nobreak >nul
    goto waiturl
    :goturl
)

REM 获取本机 IPv4 地址（取第一个非回环地址）
set "LAN_IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    if not defined LAN_IP set "LAN_IP=%%a"
)
set "LAN_IP=%LAN_IP: =%"

echo.
echo ========================================
echo  启动成功！
echo ========================================
echo  本机浏览器:   http://localhost:8501
if defined LAN_IP echo  内网链接:     http://%LAN_IP%:8501
if defined PUBLIC_URL (
    echo  ----------------------------------------
    echo  公网链接(任何人可访问):
    echo      %PUBLIC_URL%
) else if exist "cloudflared.exe" (
    echo  ----------------------------------------
    echo  [提示] 公网网址暂未取到，可稍后查看 cloudflared.log
)
echo  ----------------------------------------
if defined DASH_PASS (
    echo  网页访问密码: %DASH_PASS%  （所有链接打开后都要先输）
) else (
    echo  未设置网页密码（任何能打开链接的人都能直接访问）
)
echo ========================================
echo.
echo  - 公司内网同事 → 内网链接
echo  - 任意外网同事 → 公网链接（无需安装任何东西，打开后输密码即可）
echo  - 公网网址每次启动会变，重启后请把最新网址发给同事
echo  - 关闭此窗口会停止服务，请保持窗口开着
echo.

start "" "http://localhost:8501"

echo 按任意键停止服务并退出...
pause >nul

echo.
echo 正在停止服务...
taskkill /F /IM cloudflared.exe >nul 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8501 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>nul
endlocal

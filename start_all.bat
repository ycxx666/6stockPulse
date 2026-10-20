@echo off
REM ============================================================
REM  6stockPulse 一键启动 (PG + 采集worker + 主后端)
REM  机器重启后运行本脚本即可恢复整套系统 (无需手动逐个启动)
REM  用法: 双击运行, 或在本目录命令行执行 start_all.bat
REM ============================================================
cd /d "%~dp0"

echo [1/4] 启动 PostgreSQL 服务 ...
net start stockpulse-pg16 >nul 2>&1
if errorlevel 1 echo    (可能已在运行, 继续...)

echo [2/4] 等待 PostgreSQL 就绪 ...
"C:\Users\lenovo\pgsql\pgsql\bin\pg_isready.exe" -h 127.0.0.1 -p 5432 >nul 2>&1
if errorlevel 1 ping -n 3 127.0.0.1 >nul

echo [3/4] 启动数据采集 worker (后台最小化) ...
start "6stockPulse-worker" /min ".venv\Scripts\pythonw.exe" "workers\scheduler_runner.py"

echo [4/4] 启动主后端 API (后台最小化) ...
start "6stockPulse-api" /min ".venv\Scripts\pythonw.exe" "main.py" --port 8900

echo.
echo ============================================================
echo  启动完成!  页面: http://localhost:8900/frontend/index.html
echo  说明: worker 24h 运行(新闻全天采集; 行情/板块按交易时段)
echo ============================================================
timeout /t 3 >nul

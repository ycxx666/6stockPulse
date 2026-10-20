@echo off
REM ============================================================
REM  6stockPulse · 一键注册“开机自启 + 存活看护”计划任务
REM  用法: 右键“以管理员身份运行”本脚本
REM  说明:
REM    - 6stockPulse-Autostart : 登录时启动 PG + worker + API (start_all.bat)
REM    - 6stockPulse-Watchdog  : 每 5 分钟看护一次, 缺失则自动拉起
REM  卸载: schtasks /Delete /TN "6stockPulse-Autostart" /F
REM        schtasks /Delete /TN "6stockPulse-Watchdog" /F
REM ============================================================
set ROOT=%~dp0..

echo [1/2] 注册“登录自启”任务 ...
schtasks /Create /TN "6stockPulse-Autostart" /TR "\"%ROOT%start_all.bat\"" /SC ONLOGON /RL HIGHEST /F

echo [2/2] 注册“存活看护”任务 (每 5 分钟) ...
schtasks /Create /TN "6stockPulse-Watchdog" /TR "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%ROOT%scripts\watchdog.ps1\"" /SC MINUTE /MO 5 /RL HIGHEST /F

echo.
echo 完成! 可先手动测试看护:
echo   schtasks /Run /TN "6stockPulse-Watchdog"
echo 页面: http://localhost:8900/frontend/index.html
pause

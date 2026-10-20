# ============================================================
#  6stockPulse · 看护脚本 (watchdog)
#  作用: 每执行一次, 检查 PostgreSQL / API(:8900) / 采集 worker,
#        缺失或未运行则自动拉起。配合计划任务每 5 分钟运行实现“连续不断采集”。
#  用法: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\watchdog.ps1
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot          # scripts/ 的上一级 = 项目根
Set-Location $root

$pythonw  = Join-Path $root '.venv\Scripts\pythonw.exe'
$pgService = 'stockpulse-pg16'
$log = Join-Path $root 'logs\watchdog.log'

function Write-Log($msg) {
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    "$ts  $msg" | Out-File -FilePath $log -Append -Encoding utf8
}

# 1) PostgreSQL 服务
$pg = Get-Service -Name $pgService -ErrorAction SilentlyContinue
if (-not $pg) {
    Write-Log "PG 服务 [$pgService] 不存在"
} elseif ($pg.Status -ne 'Running') {
    Start-Service $pgService
    Write-Log "PG 服务已启动"
}

# 2) API 服务 (:8900 是否在监听)
$apiListening = Get-NetTCPConnection -LocalPort 8900 -State Listen -ErrorAction SilentlyContinue
if (-not $apiListening) {
    Start-Process -FilePath $pythonw -ArgumentList 'main.py', '--port', '8900' `
        -WorkingDirectory $root -WindowStyle Hidden
    Write-Log "API 未监听, 已拉起 main.py :8900"
}

# 3) 采集 worker (是否存在运行中的 scheduler_runner.py)
$worker = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*scheduler_runner.py*' }
if (-not $worker) {
    Start-Process -FilePath $pythonw -ArgumentList 'workers\scheduler_runner.py' `
        -WorkingDirectory $root -WindowStyle Hidden
    Write-Log "worker 未运行, 已拉起 scheduler_runner.py"
}

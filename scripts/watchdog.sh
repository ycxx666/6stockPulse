#!/usr/bin/env bash
# ============================================================
#  6stockPulse · 看护脚本 (watchdog, Linux)
#  作用: 每次执行检查 worker / 采集器 / API 容器 / API 健康,
#        缺失或未运行则自动拉起。配合 cron 每 5 分钟运行。
#  cron: */5 * * * * /home/flaskappuser/6stockPulse/scripts/watchdog.sh
# ============================================================
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/watchdog.log"
PY="$HOME/miniconda3/envs/stockpulse/bin/python"
CONDA_SH="$HOME/miniconda3/etc/profile.d/conda.sh"

mkdir -p "$ROOT/logs"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" >> "$LOG"; }

# 1) 调度 worker
if ! pgrep -f "[s]cheduler_runner.py" >/dev/null 2>&1; then
  log "worker 未运行，拉起"
  (
    source "$CONDA_SH"
    conda activate stockpulse
    cd "$ROOT"
    set -a; source .env; set +a
    setsid nohup "$PY" workers/scheduler_runner.py >> logs/worker.log 2>&1 </dev/null &
  )
  sleep 1
fi

# 2) 全市场采集器
if ! pgrep -f "[c]ollect_sina_all.py" >/dev/null 2>&1; then
  log "collector 未运行，拉起"
  (
    source "$CONDA_SH"
    conda activate stockpulse
    cd "$ROOT"
    set -a; source .env; set +a
    setsid nohup "$PY" scripts/collect_sina_all.py >> logs/collect_sina.log 2>&1 </dev/null &
  )
  sleep 1
fi

# 3) API 容器
if ! docker ps --format '{{.Names}}' | grep -qx 'stockpulse-api'; then
  log "stockpulse-api 容器未运行，拉起"
  docker start stockpulse-api >> "$LOG" 2>&1 || log "docker start 失败"
fi

# 4) API 健康检查
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8900/health || true)
if [ "$code" != "200" ]; then
  log "API health=$code，重启容器"
  docker restart stockpulse-api >> "$LOG" 2>&1 || true
fi

# 5) 错误日志检查（累计 ERROR 超阈值则告警）
err_count=$(grep -c "ERROR" "$ROOT/logs/worker.log" 2>/dev/null || true)
err_count=${err_count:-0}
if [ "$err_count" -gt 500 ] 2>/dev/null; then
  log "WARNING: worker ERROR 累计 $err_count 条，请检查"
fi

log "watchdog 完成"

#!/usr/bin/env bash
# ============================================================
#  6stockPulse · 备份脚本 (Linux)
#  作用: 备份 PostgreSQL 数据库 + 代码/配置，保留 7 天。
#  cron: 0 3 * * * /home/flaskappuser/6stockPulse/scripts/backup.sh
# ============================================================
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="$ROOT/backups"
LOG="$ROOT/logs/backup.log"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR" "$ROOT/logs"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" >> "$LOG"; }

# 1) PostgreSQL 备份
if docker ps --format '{{.Names}}' | grep -qx 'stockpulse-pg16'; then
  if docker exec stockpulse-pg16 pg_dump -U stockpulse -d stockpulse | gzip > "$BACKUP_DIR/stockpulse_pg_$DATE.sql.gz"; then
    log "PG 备份完成: stockpulse_pg_$DATE.sql.gz"
  else
    log "PG 备份失败"
  fi
else
  log "PG 容器未运行，跳过"
fi

# 2) 代码/配置备份（排除大目录与生成物）
if tar -czf "$BACKUP_DIR/stockpulse_code_$DATE.tar.gz" \
  --exclude='.git' --exclude='.venv' --exclude='venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='logs' --exclude='data' --exclude='backups' \
  --exclude='.reasonix' --exclude='6signal' \
  -C "$ROOT" . 2>/dev/null; then
  log "代码备份完成: stockpulse_code_$DATE.tar.gz"
else
  log "代码备份失败"
fi

# 3) 清理 7 天前备份
find "$BACKUP_DIR" -type f -mtime +7 -delete 2>/dev/null
log "备份任务完成"

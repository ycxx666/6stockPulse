"""打印两个连接池的实际 PG 配置，确认是否连不同库"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db.database as dbm
import infrastructure.database.postgres as infra
from config import DB as cfg1
from core.config import DB as cfg2

print("=== 根目录 config.DB (db.database 用) ===")
print("host:", cfg1.host, "port:", cfg1.port, "user:", cfg1.user, "db:", cfg1.database)
print("=== core.config.DB (infrastructure 用) ===")
print("host:", cfg2.host, "port:", cfg2.port, "user:", cfg2.user, "db:", cfg2.database)
print("=== db.database.DB_CONFIG ===")
print("host:", dbm.DB_CONFIG.host, "port:", dbm.DB_CONFIG.port, "user:", dbm.DB_CONFIG.user, "db:", dbm.DB_CONFIG.database)
print("=== infrastructure.DB_CONFIG ===")
print("host:", infra.DB_CONFIG.host, "port:", infra.DB_CONFIG.port, "user:", infra.DB_CONFIG.user, "db:", infra.DB_CONFIG.database)

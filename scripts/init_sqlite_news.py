"""初始化 SQLite 新闻库（news / stock_news / stock_ann 表）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.database.sqlite_news import init_db, DB_PATH

init_db()
print(f"SQLITE_INIT_OK: {DB_PATH}")

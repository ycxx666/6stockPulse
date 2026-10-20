"""手动触发次日预测生成（写入 prediction_result 表）"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.database import postgres as db
from domain.prediction.model import predict_next_session


async def main():
    await db.init_pool()
    try:
        result = await predict_next_session()
        print("prediction result:")
        for k, v in result.items():
            print(f"  {k}: {v}")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())

FROM python:3.10-slim

WORKDIR /app

# 系统依赖（asyncpg 需要 libpq，psycopg2 备选）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 源码
COPY . .

# 创建日志目录
RUN mkdir -p logs

EXPOSE 8900

# 默认启动 API 服务（worker 通过 command override）
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8900", "--log-level", "info"]

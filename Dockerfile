FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用缓存层）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 再复制源码
COPY . .

EXPOSE 8900

# 用 main.py 启动（正确的 app.api.server 导入路径）
CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8900"]

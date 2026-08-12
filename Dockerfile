FROM python:3.11-slim

# 로그를 버퍼링 없이 즉시 내보낸다 — 버퍼에 갇히면 운영 중 장애를 실시간으로 볼 수 없다
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

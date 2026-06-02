FROM python:3.11-slim

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY . .

# Порт для HTTP-сервера
EXPOSE 8888

CMD ["python3", "/app/api_server.py"]

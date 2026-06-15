FROM python:3.11-slim

# System deps — gcc مطلوب لـ cryptography/telethon
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libssl-dev curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# App
COPY . .

# Temp directory with write permissions
RUN mkdir -p /tmp/tg_forwarder && chmod 777 /tmp/tg_forwarder

EXPOSE 7860

CMD ["python", "app.py"]
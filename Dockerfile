# Telegram Content Forwarder - Docker Space for Hugging Face
FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create temp directory for downloads
RUN mkdir -p temp

# Expose port
EXPOSE 7860

# Run the app
CMD ["python", "app.py"]
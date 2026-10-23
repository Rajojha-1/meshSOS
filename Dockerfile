FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose HTTP port
EXPOSE 8000

# Run uvicorn server binding on all interfaces
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

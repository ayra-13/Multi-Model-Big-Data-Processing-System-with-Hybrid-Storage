FROM python:3.11.7-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    librdkafka-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -r appuser && \
    chown -R appuser:appuser /app

# Copy requirements first for better caching
COPY --chown=appuser:appuser requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY --chown=appuser:appuser src/ ./src/

# Switch to non-root user
USER appuser

# Environment variables will be provided by docker-compose
ENV KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
    MONGODB_URI=mongodb://root:example@mongodb:27017/ \
    ELASTICSEARCH_URL=http://elasticsearch:9200 \
    CASSANDRA_HOSTS=cassandra \
    S3_ACCESS_KEY=minioadmin \
    S3_SECRET_KEY=minioadmin \
    S3_ENDPOINT_URL=http://minio:9000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

CMD ["python", "src/data_processor.py"]

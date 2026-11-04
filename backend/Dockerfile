FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/opt/aca-models
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
COPY . .
RUN groupadd -g 10001 aca && useradd -u 10001 -g 10001 -M aca \
    && mkdir -p /app/data /app/chroma_db \
    && chown -R 10001:10001 /app/data /app/chroma_db \
    && chmod -R a+rX /opt/aca-models
ENV ACA_DATA_DIR=/app/data CHROMA_DB_PATH=/app/chroma_db HF_HUB_OFFLINE=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]

FROM python:3.11-slim

# curl for healthcheck, unzip for Terraform install
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Terraform 1.8.5 (detects amd64 vs arm64)
RUN ARCH=$(dpkg --print-architecture) \
    && TF_ARCH=$([ "$ARCH" = "amd64" ] && echo "amd64" || echo "arm64") \
    && curl -fsSL https://releases.hashicorp.com/terraform/1.8.5/terraform_1.8.5_linux_${TF_ARCH}.zip \
       -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin/ \
    && rm /tmp/tf.zip \
    && terraform version

WORKDIR /app

# Dependencies before source so this layer is cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image to avoid a ~90MB download on first RAG query
RUN python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .

# chroma_db and terraform_workdirs are Docker named volumes (data persists across restarts)
# terraform_plugin_cache stays in the image so baked providers are available without downloads
RUN mkdir -p /app/chroma_db /app/terraform_workdirs /app/terraform_plugin_cache

# Pre-download Terraform providers at build time into the plugin cache
# Cuts terraform init from ~60s to ~2s at runtime via hardlinks
ENV TF_PLUGIN_CACHE_DIR=/app/terraform_plugin_cache
RUN terraform -chdir=/app init -backend=false -no-color \
    && rm -rf /app/.terraform /app/.terraform.lock.hcl

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]

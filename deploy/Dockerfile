# Build from repository root:
#   docker build -f deploy/Dockerfile --target backend -t your-registry/kubeeasy-api:latest .
#   docker build -f deploy/Dockerfile --target frontend -t your-registry/kubeeasy-ui:latest .

FROM nginx:1.25-alpine AS frontend
COPY deploy/nginx.minimal.conf /etc/nginx/conf.d/default.conf
COPY client/ /usr/share/nginx/html/

# API image (default target when --target is omitted = last stage)
FROM python:3.12-slim AS backend

RUN apt-get update && apt-get install -y curl jq && \
    curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x kubectl && mv kubectl /usr/local/bin/ && \
    curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
ENV SCRIPT_DIR=/app/scripts
ENV CONFIG_DIR=/data/configs

CMD ["python", "api/server.py"]

FROM node:22-alpine AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY isot ./isot
RUN pip install --no-cache-dir .
COPY templates ./templates
COPY examples ./examples
COPY docs ./docs
COPY isot.example.yaml ./
COPY --from=frontend /web/dist ./web/dist
# Plantillas y demo de la herramienta. El repositorio de datos se monta en /data
# (ver docker-compose.yml); sin montaje se sirve la demo de solo lectura.
ENV ISOT_HOME=/app \
    ISOT_ROOT=/app/examples/demo-repo
EXPOSE 8000
CMD ["python", "-m", "isot", "web", "--host", "0.0.0.0", "--port", "8000"]

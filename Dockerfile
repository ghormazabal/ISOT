FROM node:22-alpine AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY isot ./isot
RUN pip install --no-cache-dir .
COPY sites.yaml ./sites.yaml
COPY sites ./sites
COPY cluster-templates ./cluster-templates
COPY --from=frontend /web/dist ./web/dist
ENV ISOT_ROOT=/app
EXPOSE 8000
CMD ["uvicorn", "isot.web:app", "--host", "0.0.0.0", "--port", "8000"]

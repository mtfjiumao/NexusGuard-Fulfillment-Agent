FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=frontend-build /app/src/nexusguard/web/dist ./src/nexusguard/web/dist
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home appuser

COPY alembic.ini ./
COPY migrations ./migrations
USER appuser
EXPOSE 8000
CMD ["uvicorn", "nexusguard.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

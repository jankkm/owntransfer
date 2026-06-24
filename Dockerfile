FROM node:20-slim AS frontend

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

COPY tailwind.config.js ./
COPY frontend/tailwind.css frontend/tailwind.css
COPY app/templates app/templates
COPY app/static app/static
RUN npm run build:css

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

COPY app app
COPY --from=frontend /build/app/static/tailwind.css app/static/tailwind.css
COPY .env.example .env.example
RUN pybabel compile -d app/locales

RUN mkdir -p /data/uploads

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

# ---- Stage 1: build web admin (Vite -> botcheckv2/backend/static) ----
FROM node:20-slim AS frontend
WORKDIR /build/botcheckv2/frontend
COPY botcheckv2/frontend/package.json ./
RUN npm install
COPY botcheckv2/frontend/ ./
RUN npm run build

# ---- Stage 2: backend FastAPI + bot Telegram ----
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app/botcheckv2/backend
COPY botcheckv2/backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY botcheckv2/backend/ ./
# Web admin vua build o stage 1
COPY --from=frontend /build/botcheckv2/backend/static ./static
# Tuyet doi khong mang DB / .env cua may cu len image
RUN rm -f data.db data.db-shm data.db-wal .env
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

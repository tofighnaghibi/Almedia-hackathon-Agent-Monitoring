# Argus: one container, frontend + API. Build: docker build -t argus .  Run: docker run -p 7860:7860 argus
# Optional LLM: docker run -p 7860:7860 -e LLM_BASE_URL=... -e LLM_API_KEY=... -e LLM_MODEL=... argus

# --- stage 1: build the frontend
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- stage 2: Python runtime serving the API and frontend/dist at /
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/argus ./argus
# Precompute what is identical for every session (fitted default world, launch forecast + backtest) so a cold start
# on a slow CPU only unpickles it; keyed by a hash of the argus sources, ignored if stale.
RUN ARGUS_NO_DOTENV=1 python -m argus.precompute
COPY --from=frontend /app/frontend/dist /app/frontend/dist
# Hugging Face Spaces runs containers as uid 1000
RUN useradd -m -u 1000 user && chown -R user /app
USER user
EXPOSE 7860
CMD ["sh", "-c", "uvicorn argus.api:app --host 0.0.0.0 --port ${PORT:-7860}"]

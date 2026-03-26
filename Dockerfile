# ──────────────────────────────────────────────────────────────────────
# DevOps Incident Responder — OpenEnv Environment
# Build:  docker build -t devops-incident-responder .
# Run:    docker run -p 7860:7860 devops-incident-responder
# With OpenAI key:
#         docker run -p 7860:7860 -e OPENAI_API_KEY=sk-... devops-incident-responder
# ──────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Metadata
LABEL maintainer="devops-incident-responder"
LABEL description="OpenEnv: DevOps Incident Responder — microservice cluster debug environment"
LABEL version="1.0.0"
LABEL org.opencontainers.image.title="devops-incident-responder"

# ── System deps ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Create non-root user (HuggingFace Spaces requirement) ─────────────
RUN useradd -m -u 1000 appuser

# ── Working directory ─────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies (layer-cached) ───────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Copy source ───────────────────────────────────────────────────────
COPY --chown=appuser:appuser . .

# ── Switch to non-root ────────────────────────────────────────────────
USER appuser

# ── Environment ───────────────────────────────────────────────────────
ENV PORT=7860
ENV WORKERS=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# ── Expose port ───────────────────────────────────────────────────────
EXPOSE 7860

# ── Health check ─────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:7860/health || exit 1

# ── Start server ─────────────────────────────────────────────────────
CMD ["python", "-m", "uvicorn", "server.app:app", \
     "--host", "0.0.0.0", \
     "--port", "7860", \
     "--workers", "1", \
     "--log-level", "info"]

# AURA Backend Core – Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY backend_core/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_core/ ./backend_core/

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# NOTE: --workers is intentionally kept at 1.
# _density_state in routes.py is a module-level in-process dict.
# Multiple workers would maintain independent snapshots that diverge silently.
# Scale horizontally only after migrating state to Redis.
CMD ["uvicorn", "backend_core.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]


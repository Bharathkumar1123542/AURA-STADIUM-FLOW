# AURA Unified Cloud Run Container (Backend + Dashboard)
FROM python:3.12-slim

WORKDIR /app

# Install dependencies for the backend
COPY backend_core/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend code and the static dashboard
COPY backend_core/ ./backend_core/
COPY dashboard/ ./dashboard/

ENV PYTHONUNBUFFERED=1

# Cloud Run dynamically assigns a port to this environment variable (default 8080)
# We expose it for documentation purposes, but Cloud Run controls mapping
EXPOSE 8080

# Run uvicorn on $PORT, restricted to 1 worker to preserve in-memory density state
CMD exec uvicorn backend_core.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1

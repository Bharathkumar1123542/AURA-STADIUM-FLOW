# AURA Camera Engine – Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache optimization)
COPY camera_engine/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY camera_engine/ ./camera_engine/
COPY backend_core/  ./backend_core/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "camera_engine.main", "--backend", "http://backend_core:8000"]

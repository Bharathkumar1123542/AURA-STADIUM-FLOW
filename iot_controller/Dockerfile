# AURA IoT Controller – Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY iot_controller/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY iot_controller/ ./iot_controller/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "iot_controller.led_driver"]

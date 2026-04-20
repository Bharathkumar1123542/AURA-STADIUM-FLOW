"""
AURA IoT Controller – MQTT Client
====================================
Manages bi-directional MQTT communication between the backend and LED hardware.

WHY MQTT over HTTP:
  MQTT is the IoT standard: lightweight, broker-based, supports QoS levels.
  At-least-once delivery (QoS 1) ensures LED state changes aren't lost.

Topics:
  aura/led/{section_id}/set     ← backend publishes LED commands
  aura/led/{section_id}/state   ← hardware publishes current state (ack)
  aura/alerts/{section_id}      ← backend publishes nudge alerts

Broker: Mosquitto (Docker service "mqtt_broker")
"""

import json
import logging
import os
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False
    logger.warning("paho-mqtt not installed; using mock MQTT client.")


# ---------------------------------------------------------------------------
# Mock MQTT client (used when paho is unavailable or in CI)
# ---------------------------------------------------------------------------

class MockMQTTClient:
    """Simulates paho.mqtt.client interface for local testing."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self._subscriptions: dict = {}
        logger.info("MockMQTTClient created (id=%s)", client_id)

    def connect(self, host: str, port: int, keepalive: int = 60) -> None:
        logger.info("MOCK MQTT: connected to %s:%d", host, port)

    def subscribe(self, topic: str, qos: int = 1) -> None:
        logger.info("MOCK MQTT: subscribed to %s (QoS %d)", topic, qos)
        self._subscriptions[topic] = qos

    def publish(self, topic: str, payload: str, qos: int = 1) -> None:
        logger.info("MOCK MQTT PUBLISH → [%s]: %s", topic, payload)

    def loop_start(self) -> None:
        pass

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        logger.info("MOCK MQTT: disconnected")


# ---------------------------------------------------------------------------
# AURA MQTT Client
# ---------------------------------------------------------------------------

class AuraMQTTClient:
    """
    Thread-safe MQTT client wrapper.

    Responsibilities:
    - Connect to broker with auto-reconnect
    - Publish LED state commands to IoT hardware
    - Subscribe to hardware acknowledgements
    - Fan out messages to registered handlers
    """

    BROKER_HOST = os.environ.get("MQTT_HOST", "mqtt_broker")
    BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))
    CLIENT_ID = "aura-iot-controller"

    def __init__(self, on_message_callback: Optional[Callable] = None):
        self._callback = on_message_callback
        self._connected = False

        if PAHO_AVAILABLE:
            self._client = mqtt.Client(client_id=self.CLIENT_ID)
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect
        else:
            self._client = MockMQTTClient(self.CLIENT_ID)

    def connect(self) -> None:
        """Connect to MQTT broker with retry logic."""
        for attempt in range(5):
            try:
                self._client.connect(self.BROKER_HOST, self.BROKER_PORT, keepalive=60)
                self._client.loop_start()
                # NOTE: _connected is set to True inside _on_connect callback,
                # not here.  Setting it prematurely (before the broker ACKs)
                # caused publishled commands to be treated as "connected"
                # even when the TCP handshake was still pending.
                logger.info("✅ MQTT loop started – waiting for broker ACK…")
                return
            except Exception as exc:
                wait = 2 ** attempt
                logger.warning("MQTT connect attempt %d failed: %s – retrying in %ds", attempt+1, exc, wait)
                time.sleep(wait)
        logger.error("❌ MQTT connection exhausted. Running in degraded mode.")


    def publish_led_command(self, section_id: str, state: str, metadata: Optional[dict] = None) -> None:
        """
        Publish LED state command to a section's hardware.

        Args:
            section_id: Stadium section (A-F)
            state:      "RED" | "GREEN" | "AMBER"
            metadata:   Optional extra info for hardware (e.g., animation pattern)
        """
        topic = f"aura/led/{section_id}/set"
        payload = {
            "section_id": section_id,
            "state": state,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        self._client.publish(topic, json.dumps(payload), qos=1)
        logger.info("💡 LED %s → %s", section_id, state)

    def publish_alert(self, section_id: str, message: str, nudge_action: dict) -> None:
        """Broadcast a fan alert to section-specific topic."""
        topic = f"aura/alerts/{section_id}"
        payload = {
            "message": message,
            "nudge_action": nudge_action,
            "timestamp": time.time(),
        }
        self._client.publish(topic, json.dumps(payload), qos=1)

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False

    # ------------------------------------------------------------------
    # Paho callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            self._connected = True
            # Subscribe to hardware state acknowledgements
            client.subscribe("aura/led/+/state", qos=1)
            logger.info("MQTT subscribed to LED state acks.")
        else:
            logger.error("MQTT connect error code: %d", rc)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode())
            logger.debug("MQTT RX [%s]: %s", msg.topic, payload)
            if self._callback:
                self._callback(msg.topic, payload)
        except json.JSONDecodeError:
            logger.warning("Non-JSON MQTT message on %s", msg.topic)

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        if rc != 0:
            logger.warning("MQTT unexpected disconnect (rc=%d). Auto-reconnect active.", rc)

# mqtt_subscriber.py
import json
import logging
import paho.mqtt.client as mqtt
from db import SessionLocal
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "ingps/#"   # ingps/GW_LN01/DEV_XXXX, ingps/mvpmodel


def handle_mfg_data(payload: dict):
    """
    STM32 Gateway에서 오는 mfg_data 처리
    payload 예시:
    {
        "gateway_id": "GW_LN01",
        "device_id":  "DEV_0123",
        "mac": "AA:BB:CC:DD:EE:FF"
    }
    """
    db = SessionLocal()
    try:
        gateway_id = payload.get("gateway_id")
        device_id  = payload.get("device_id")
        mac        = payload.get("mac", "")

        if not gateway_id or not device_id:
            logger.warning("gateway_id 또는 device_id 누락: %s", payload)
            return

        # 1) Gateway last_seen 갱신
        result = db.execute(text("""
            UPDATE line_gateway
            SET last_seen_at = NOW(), status = 'Normal'
            WHERE gateway_id = :gateway_id
        """), {"gateway_id": gateway_id})

        if result.rowcount == 0:
            logger.warning("[MQTT] Gateway 미등록: %s", gateway_id)

        # 2) Device last_seen 갱신 (등록된 device만)
        result = db.execute(text("""
            UPDATE device
            SET last_seen_at = NOW()
            WHERE device_id = :device_id
        """), {"device_id": device_id})

        if result.rowcount == 0:
            logger.info("[MQTT] 미등록 Device 감지: %s (mac=%s)", device_id, mac)
        else:
            logger.info("[MQTT] Device 감지 ✅ | id=%s mac=%s", device_id, mac)

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error("[MQTT] DB 처리 실패: %s", e)
    finally:
        db.close()


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("[MQTT] Broker 연결 성공")
        client.subscribe(MQTT_TOPIC)
        logger.info("[MQTT] 구독 시작: %s", MQTT_TOPIC)
    else:
        logger.error("[MQTT] 연결 실패 rc=%d", rc)


def handle_temperature(payload: dict):
    """
    ingps/mvpmodel 토픽 처리
    payload: {"device_id": "esp_32", "temp1": 25.3, "temp2": 26.1,
              "angle_x": 0.1, "angle_y": -0.2, "angle_z": 1.5}
    """
    db = SessionLocal()
    try:
        device_id = payload.get("device_id")
        temp1     = payload.get("temp1")
        temp2     = payload.get("temp2")
        angle_x   = payload.get("angle_x")
        angle_y   = payload.get("angle_y")
        angle_z   = payload.get("angle_z")

        if device_id is None or temp1 is None:
            logger.warning("[MQTT] temperature payload 누락: %s", payload)
            return

        db.execute(text("""
            INSERT INTO temperature_log (device_id, temp1, temp2, angle_x, angle_y, angle_z, created_at)
            VALUES (:device_id, :temp1, :temp2, :angle_x, :angle_y, :angle_z, NOW())
        """), {"device_id": device_id, "temp1": temp1, "temp2": temp2,
               "angle_x": angle_x, "angle_y": angle_y, "angle_z": angle_z})

        db.commit()
        logger.info("[MQTT] 데이터 저장 ✅ | device=%s temp1=%.2f temp2=%.2f angle=(%s,%s,%s)",
                    device_id, temp1, temp2, angle_x, angle_y, angle_z)

    except Exception as e:
        db.rollback()
        logger.error("[MQTT] DB 저장 실패: %s", e)
    finally:
        db.close()


def on_message(client, userdata, msg):
    logger.info("[MQTT] 수신 topic=%s", msg.topic)
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        if msg.topic == "ingps/mvpmodel":
            handle_temperature(payload)
        else:
            handle_mfg_data(payload)
    except json.JSONDecodeError as e:
        logger.error("[MQTT] JSON 파싱 실패: %s | raw=%s", e, msg.payload)


def start_mqtt():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    logger.info("[MQTT] Subscriber 시작...")
    client.loop_forever()


if __name__ == "__main__":
    start_mqtt()
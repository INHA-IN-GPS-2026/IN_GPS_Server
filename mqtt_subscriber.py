# mqtt_subscriber.py
import json
import logging
from typing import Optional

import paho.mqtt.client as mqtt
from db import SessionLocal
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MQTT_HOST = "localhost"
MQTT_PORT = 1883
# 토픽 구조 (와일드카드 # 로 통합 구독):
#   ingps/sensor          : ESP RMS+온도 페이로드 (게이트웨이가 mfg_data 파싱해 publish)
#   ingps/mvpmodel        : 레거시 (기존 호환용)
#   ingps/<gateway>/<dev> : gateway/device hello (last_seen 갱신)
MQTT_TOPIC = "ingps/#"


# ============================================================
# byte_id → device_id 매핑
#   ESP 펌웨어 mfg_data 13번째 바이트(0x01~0x0A) → "esp_32_0" ~ "esp_32_9"
#   매핑 룰: device_id_suffix = byte_id - 1
# ============================================================
def esp_byte_to_device_id(byte_id):
    # type: (Optional[int]) -> Optional[str]
    """ESP 메인패클처 byte → device_id 문자열. 범위를 벗어나면 None."""
    if byte_id is None:
        return None
    if 0 <= byte_id <= 9:
        return "esp_32_{0}".format(byte_id)
    return None


def handle_mfg_data(payload: dict):
    """
    STM32 Gateway hello/heartbeat 처리 (last_seen 갱신용).
    {
        "gateway_id": "GW_LN01",
        "device_id":  "esp_32_0",
        "mac":        "CA:BB:CC:DD:EE:01"
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

        result = db.execute(text("""
            UPDATE line_gateway
            SET last_seen_at = NOW(), status = 'Normal'
            WHERE gateway_id = :gateway_id
        """), {"gateway_id": gateway_id})

        if result.rowcount == 0:
            logger.warning("[MQTT] Gateway 미등록: %s", gateway_id)

        result = db.execute(text("""
            UPDATE device
            SET last_seen_at = NOW()
            WHERE device_id = :device_id
        """), {"device_id": device_id})

        if result.rowcount == 0:
            logger.info("[MQTT] 미등록 Device 감지: %s (mac=%s)", device_id, mac)
        else:
            logger.info("[MQTT] Device hello ✅ | id=%s mac=%s", device_id, mac)

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error("[MQTT] DB 처리 실패: %s", e)
    finally:
        db.close()


def _classify_event(rms_x, rms_y, rms_z):
    # type: (Optional[int], Optional[int], Optional[int]) -> str
    """RMS 진동 크기로 간단 임계치 분류. 임계치는 추후 ML로 대체 가능."""
    rms_values = [v for v in (rms_x, rms_y, rms_z) if v is not None]
    if not rms_values:
        return "normal"
    peak = max(rms_values)
    if peak >= 5000:   # 5g 이상 → warning
        return "warning"
    return "normal"


def handle_sensor(payload: dict):
    """
    ESP RMS + 온도 페이로드 처리.

    Gateway가 mfg_data를 파싱해 보내는 JSON payload:
    {
        "gateway_id":  "GW_LN01",      (옵션)
        "esp_byte_id": 1,              ← mfg_data 13번째 바이트 그대로
        "temp1":       25.30,
        "temp2":       26.10,
        "rms_x":       20,             (mg, uint16)
        "rms_y":       18,
        "rms_z":       15
    }
    또는 device_id를 게이트웨이가 이미 결정해 보낼 경우:
        "device_id": "esp_32_0" 도 우선 인식.
    """
    db = SessionLocal()
    try:
        device_id = payload.get("device_id")
        if not device_id:
            byte_id = payload.get("esp_byte_id")
            device_id = esp_byte_to_device_id(byte_id)

        if not device_id:
            logger.warning("[MQTT] device_id/esp_byte_id 매핑 실패: %s", payload)
            return

        temp1 = payload.get("temp1")
        temp2 = payload.get("temp2")
        rms_x = payload.get("rms_x")
        rms_y = payload.get("rms_y")
        rms_z = payload.get("rms_z")

        event = payload.get("event") or _classify_event(rms_x, rms_y, rms_z)

        db.execute(text("""
            INSERT INTO temperature_log
                (device_id, temp1, temp2, rms_x, rms_y, rms_z, event, created_at)
            VALUES
                (:device_id, :temp1, :temp2, :rms_x, :rms_y, :rms_z, :event, NOW())
        """), {
            "device_id": device_id,
            "temp1": temp1, "temp2": temp2,
            "rms_x": rms_x, "rms_y": rms_y, "rms_z": rms_z,
            "event": event,
        })

        db.execute(text("""
            UPDATE device
            SET last_seen_at = NOW()
            WHERE device_id = :device_id
        """), {"device_id": device_id})

        db.commit()
        logger.info(
            "[MQTT] sensor ✅ | %s T1=%.2f T2=%.2f RMS=(%s,%s,%s) ev=%s",
            device_id,
            temp1 if temp1 is not None else float("nan"),
            temp2 if temp2 is not None else float("nan"),
            rms_x, rms_y, rms_z, event,
        )

    except Exception as e:
        db.rollback()
        logger.error("[MQTT] sensor 저장 실패: %s | payload=%s", e, payload)
    finally:
        db.close()


def handle_temperature_legacy(payload: dict):
    """
    레거시 ingps/mvpmodel 토픽 — angle_x/y/z 키로 들어오는 옛 페이로드도
    DB 컬럼 rename에 맞춰 rms_x/y/z 자리에 저장.
    """
    # angle_* 키가 들어오면 rms_* 자리로 옮겨 sensor 핸들러에 위임
    aliased = dict(payload)
    if "angle_x" in aliased and "rms_x" not in aliased:
        aliased["rms_x"] = aliased.pop("angle_x")
    if "angle_y" in aliased and "rms_y" not in aliased:
        aliased["rms_y"] = aliased.pop("angle_y")
    if "angle_z" in aliased and "rms_z" not in aliased:
        aliased["rms_z"] = aliased.pop("angle_z")
    handle_sensor(aliased)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("[MQTT] Broker 연결 성공")
        client.subscribe(MQTT_TOPIC)
        logger.info("[MQTT] 구독 시작: %s", MQTT_TOPIC)
    else:
        logger.error("[MQTT] 연결 실패 rc=%d", rc)


def on_message(client, userdata, msg):
    logger.info("[MQTT] 수신 topic=%s", msg.topic)
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except json.JSONDecodeError as e:
        logger.error("[MQTT] JSON 파싱 실패: %s | raw=%s", e, msg.payload)
        return

    if msg.topic == "ingps/sensor":
        handle_sensor(payload)
    elif msg.topic == "ingps/mvpmodel":
        handle_temperature_legacy(payload)
    else:
        handle_mfg_data(payload)


def start_mqtt():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    logger.info("[MQTT] Subscriber 시작...")
    client.loop_forever()


if __name__ == "__main__":
    start_mqtt()

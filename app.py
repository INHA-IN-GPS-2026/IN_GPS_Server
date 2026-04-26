# app.py
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from datetime import datetime
from db import get_db
from typing import Optional

app = FastAPI(title="IN-GPS API", version="0.1.0")


# ---------- DTOs ----------
class DummyLogIn(BaseModel):
    device_id: str = Field(..., examples=["DEV_2001"])
    reboot_count: int = 0
    temp_out_c: Optional[float] = 25.0
    temp_core_c: Optional[float] = 60.0
    fault_grade: int = 0


class TempLogIn(BaseModel):
    device_id: str = Field(..., examples=["esp_32"])
    temp1: float
    temp2: Optional[float] = None
    event: str = Field("normal", examples=["normal"], pattern="^(normal|warning|disconnected)$")


class TempLogUpdate(BaseModel):
    temp1: float
    temp2: Optional[float] = None
    event: str = Field(..., examples=["normal"], pattern="^(normal|warning|disconnected)$")


# ---------- Health ----------
@app.get("/health")
def health(db: Session = Depends(get_db)):
    # DB 연결 확인
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True, "db": "ok"}
    except Exception as e:
        return {"ok": False, "db": "fail", "error": str(e)}


# ---------- Read APIs (Mobile 용) ----------
@app.get("/lines")
def list_lines(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT line_id, line_name, created_at, updated_at
        FROM line
        ORDER BY line_id
    """)).mappings().all()
    return {"items": list(rows)}


@app.get("/equipments")
def list_equipments(line_id:Optional[str] = None, db: Session = Depends(get_db)):
    if line_id:
        rows = db.execute(text("""
            SELECT equipment_id, line_id, equipment_name, created_at, updated_at
            FROM equipment
            WHERE line_id = :line_id
            ORDER BY equipment_id
        """), {"line_id": line_id}).mappings().all()
    else:
        rows = db.execute(text("""
            SELECT equipment_id, line_id, equipment_name, created_at, updated_at
            FROM equipment
            ORDER BY equipment_id
        """)).mappings().all()
    return {"items": list(rows)}


@app.get("/devices")
def list_devices(equipment_id: str | None = None, db: Session = Depends(get_db)):
    if equipment_id:
        rows = db.execute(text("""
            SELECT device_id, equipment_id, status, installed_on, last_seen_at, created_at, updated_at
            FROM device
            WHERE equipment_id = :equipment_id
            ORDER BY device_id
        """), {"equipment_id": equipment_id}).mappings().all()
    else:
        rows = db.execute(text("""
            SELECT device_id, equipment_id, status, installed_on, last_seen_at, created_at, updated_at
            FROM device
            ORDER BY device_id
        """)).mappings().all()
    return {"items": list(rows)}


@app.get("/devices/{device_id}/logs")
def get_device_logs(device_id: str, limit: int = 200, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 1000))
    rows = db.execute(text("""
        SELECT log_id, device_id, reboot_count, temp_out_c, temp_core_c, fault_grade, created_at
        FROM device_log
        WHERE device_id = :device_id
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"device_id": device_id, "limit": limit}).mappings().all()
    return {"items": list(rows)}


# ---------- Temperature ----------
@app.get("/temperature/chart")
def get_temperature_chart(device_id: str, days: int = 1, db: Session = Depends(get_db)):
    """
    모바일 차트용 엔드포인트.
    - days <= 7 : raw 데이터 (분 단위)
    - days >  7 : 일별 집계 (avg/min/max) → 최대 365개
    """
    days = max(1, min(days, 365))

    if days <= 7:
        rows = db.execute(text("""
            SELECT id, device_id,
                   temp1, temp2,
                   angle_x, angle_y, angle_z,
                   event, created_at
            FROM temperature_log
            WHERE device_id = :device_id
              AND created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
            ORDER BY created_at DESC
        """), {"device_id": device_id, "days": days}).mappings().all()
        return {"items": list(rows), "aggregated": False}
    else:
        rows = db.execute(text("""
            SELECT
                0                            AS id,
                device_id,
                ROUND(AVG(temp1), 2)         AS temp1,
                ROUND(AVG(temp2), 2)         AS temp2,
                ROUND(MIN(temp1), 2)         AS temp1_min,
                ROUND(MAX(temp1), 2)         AS temp1_max,
                ROUND(MIN(temp2), 2)         AS temp2_min,
                ROUND(MAX(temp2), 2)         AS temp2_max,
                NULL                         AS angle_x,
                NULL                         AS angle_y,
                NULL                         AS angle_z,
                CASE
                    WHEN SUM(CASE WHEN event = 'disconnected' THEN 1 ELSE 0 END) > 0 THEN 'disconnected'
                    WHEN SUM(CASE WHEN event = 'warning'      THEN 1 ELSE 0 END) > 0 THEN 'warning'
                    ELSE 'normal'
                END                          AS event,
                CONCAT(DATE(created_at), 'T12:00:00') AS created_at
            FROM temperature_log
            WHERE device_id = :device_id
              AND created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
            GROUP BY DATE(created_at), device_id
            ORDER BY created_at DESC
        """), {"device_id": device_id, "days": days}).mappings().all()
        return {"items": list(rows), "aggregated": True}


@app.get("/temperature")
def get_temperature(device_id: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 1000))
    if device_id:
        rows = db.execute(text("""
            SELECT id, device_id, temp1, temp2, angle_x, angle_y, angle_z, event, created_at
            FROM temperature_log
            WHERE device_id = :device_id
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"device_id": device_id, "limit": limit}).mappings().all()
    else:
        rows = db.execute(text("""
            SELECT id, device_id, temp1, temp2, angle_x, angle_y, angle_z, event, created_at
            FROM temperature_log
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return {"items": list(rows)}


@app.post("/temperature", status_code=201)
def post_temperature(payload: TempLogIn, db: Session = Depends(get_db)):
    result = db.execute(text("""
        INSERT INTO temperature_log (device_id, temp1, temp2, event)
        VALUES (:device_id, :temp1, :temp2, :event)
    """), {"device_id": payload.device_id, "temp1": payload.temp1, "temp2": payload.temp2, "event": payload.event})
    db.commit()
    return {"ok": True, "id": result.lastrowid, "device_id": payload.device_id}


@app.patch("/temperature/{log_id}")
def update_temperature(log_id: int, payload: TempLogUpdate, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT id FROM temperature_log WHERE id = :id"), {"id": log_id}).first()
    if not row:
        raise HTTPException(status_code=404, detail="temperature_log id not found")
    db.execute(text("""
        UPDATE temperature_log
        SET temp1 = :temp1, temp2 = :temp2, event = :event
        WHERE id = :id
    """), {"temp1": payload.temp1, "temp2": payload.temp2, "event": payload.event, "id": log_id})
    db.commit()
    return {"ok": True, "id": log_id, "event": payload.event}


# ---------- Dummy write APIs (게이트웨이 없이 테스트) ----------
@app.post("/debug/bootstrap")
def bootstrap_minimal(db: Session = Depends(get_db)):
    """
    최소 더미 구조 생성:
    LN_01 -> EQ_B01 -> DEV_2001
    """
    # line
    db.execute(text("""
        INSERT INTO line (line_id, line_name)
        VALUES ('LN_01', 'A조립라인')
        ON DUPLICATE KEY UPDATE line_name=VALUES(line_name)
    """))

    # equipment
    db.execute(text("""
        INSERT INTO equipment (equipment_id, line_id, equipment_name)
        VALUES ('EQ_B01', 'LN_01', 'B설비')
        ON DUPLICATE KEY UPDATE line_id=VALUES(line_id), equipment_name=VALUES(equipment_name)
    """))

    # device
    db.execute(text("""
        INSERT INTO device (device_id, equipment_id, status, installed_on, last_seen_at)
        VALUES ('DEV_2001', 'EQ_B01', 'Normal', CURDATE(), NOW())
        ON DUPLICATE KEY UPDATE equipment_id=VALUES(equipment_id), last_seen_at=NOW()
    """))

    db.commit()
    return {"ok": True}


@app.post("/debug/log")
def insert_dummy_log(payload: DummyLogIn, db: Session = Depends(get_db)):
    """
    더미 로그 넣기 + device last_seen/status 갱신
    """
    # device 존재 확인
    device = db.execute(text("""
        SELECT device_id FROM device WHERE device_id = :device_id
    """), {"device_id": payload.device_id}).mappings().first()

    if not device:
        raise HTTPException(status_code=404, detail="device_id not found. Call /debug/bootstrap first or create device.")

    # status 룰 (임시): fault_grade >= 5면 Warning
    status = "Warning" if payload.fault_grade >= 5 else "Normal"

    db.execute(text("""
        UPDATE device
        SET last_seen_at = NOW(), status = :status
        WHERE device_id = :device_id
    """), {"device_id": payload.device_id, "status": status})

    db.execute(text("""
        INSERT INTO device_log (device_id, reboot_count, temp_out_c, temp_core_c, fault_grade, created_at)
        VALUES (:device_id, :reboot_count, :temp_out_c, :temp_core_c, :fault_grade, NOW())
    """), {
        "device_id": payload.device_id,
        "reboot_count": payload.reboot_count,
        "temp_out_c": payload.temp_out_c,
        "temp_core_c": payload.temp_core_c,
        "fault_grade": payload.fault_grade
    })

    db.commit()
    return {"ok": True, "device_id": payload.device_id, "status": status, "ts": datetime.now().isoformat()}
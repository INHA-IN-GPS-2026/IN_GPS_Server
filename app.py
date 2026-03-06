# app.py
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from datetime import datetime
from db import get_db

app = FastAPI(title="IN-GPS API", version="0.1.0")


# ---------- DTOs ----------
class DummyLogIn(BaseModel):
    device_id: str = Field(..., examples=["DEV_2001"])
    reboot_count: int = 0
    temp_out_c: float | None = 25.0
    temp_core_c: float | None = 60.0
    fault_grade: int = 0


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
def list_equipments(line_id: str | None = None, db: Session = Depends(get_db)):
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
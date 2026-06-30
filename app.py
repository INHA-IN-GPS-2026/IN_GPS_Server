# app.py
import csv
import io
import os

from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response, Cookie
from fastapi.responses import StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from datetime import datetime
from db import get_db
from typing import Optional

import auth

app = FastAPI(title="IN-GPS API", version="0.3.0")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


# ---------- DTOs ----------
class DummyLogIn(BaseModel):
    device_id: str = Field(..., examples=["DEV_2001"])
    reboot_count: int = 0
    temp_out_c: Optional[float] = 25.0
    temp_core_c: Optional[float] = 60.0
    fault_grade: int = 0


class SensorLogIn(BaseModel):
    """ESP RMS + 온도 페이로드. mqtt_subscriber와 동일 스키마."""
    device_id: str = Field(..., examples=["esp_32_0"])
    temp1: Optional[float] = None
    temp2: Optional[float] = None
    rms_x: Optional[int]   = Field(None, ge=0, le=65535)
    rms_y: Optional[int]   = Field(None, ge=0, le=65535)
    rms_z: Optional[int]   = Field(None, ge=0, le=65535)
    event: str = Field("normal", pattern="^(normal|warning|disconnected)$")


class SensorLogUpdate(BaseModel):
    temp1: Optional[float] = None
    temp2: Optional[float] = None
    rms_x: Optional[int]   = Field(None, ge=0, le=65535)
    rms_y: Optional[int]   = Field(None, ge=0, le=65535)
    rms_z: Optional[int]   = Field(None, ge=0, le=65535)
    event: str = Field(..., pattern="^(normal|warning|disconnected)$")


class SignupIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


# ---------- Auth ----------
def current_user(ingps_session: Optional[str] = Cookie(default=None)) -> str:
    """세션 쿠키를 검증해 username을 반환. 없거나 무효면 401."""
    username = auth.read_session(ingps_session) if ingps_session else None
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


@app.post("/auth/signup", status_code=201)
def signup(payload: SignupIn, db: Session = Depends(get_db)):
    exists = db.execute(
        text("SELECT user_id FROM web_user WHERE username = :u"),
        {"u": payload.username},
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")
    pw_hash, salt, iters = auth.hash_password(payload.password)
    db.execute(text("""
        INSERT INTO web_user (username, pw_hash, pw_salt, pw_iters)
        VALUES (:u, :h, :s, :i)
    """), {"u": payload.username, "h": pw_hash, "s": salt, "i": iters})
    db.commit()
    return {"ok": True, "username": payload.username}


@app.post("/auth/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT pw_hash, pw_salt, pw_iters FROM web_user WHERE username = :u
    """), {"u": payload.username}).mappings().first()
    # 사용자 유무와 무관하게 동일 메시지 → 계정 존재 여부 노출 방지
    if not row or not auth.verify_password(
        payload.password, row["pw_hash"], row["pw_salt"], row["pw_iters"]
    ):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    db.execute(text("UPDATE web_user SET last_login = NOW() WHERE username = :u"),
               {"u": payload.username})
    db.commit()
    token = auth.make_session(payload.username)
    response.set_cookie(
        key=auth.COOKIE_NAME, value=token,
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
        # 운영(HTTPS)에선 secure=True 권장. 로컬 http 테스트 위해 기본 False.
        secure=False,
    )
    return {"ok": True, "username": payload.username}


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@app.get("/auth/me")
def me(username: str = Depends(current_user)):
    return {"username": username}


# ---------- Health ----------
@app.get("/health")
def health(db: Session = Depends(get_db)):
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
def list_equipments(line_id: Optional[str] = None, db: Session = Depends(get_db)):
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
def list_devices(equipment_id: Optional[str] = None, db: Session = Depends(get_db)):
    # 전체 device 목록. status는 응답 시점에 동적 결정:
    #   last_seen_at이 최근 5초 이내 → 저장된 status 그대로
    #   그 외(stale 또는 NULL) → 'Disconnected'
    base_sql = """
        SELECT device_id, equipment_id,
               CASE WHEN last_seen_at > NOW() - INTERVAL 15 SECOND THEN status
                    ELSE 'Disconnected' END AS status,
               installed_on, last_seen_at, created_at, updated_at
        FROM device
    """
    if equipment_id:
        rows = db.execute(text(base_sql + """
            WHERE equipment_id = :equipment_id
            ORDER BY CAST(SUBSTRING_INDEX(device_id, '_', -1) AS UNSIGNED)
        """), {"equipment_id": equipment_id}).mappings().all()
    else:
        rows = db.execute(text(base_sql + " ORDER BY CAST(SUBSTRING_INDEX(device_id, '_', -1) AS UNSIGNED)")).mappings().all()
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


# ---------- Sensor / Chart (RMS + 온도) ----------

# bucket → MySQL time expression. 화이트리스트라 SQL 직접 삽입 안전.
_BUCKET_TIME_SQL = {
    "1m": "DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:00')",
    "5m": "FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(created_at)/300)*300)",
    "1h": "DATE_FORMAT(created_at, '%Y-%m-%d %H:00:00')",
    "1d": "DATE_FORMAT(created_at, '%Y-%m-%d 12:00:00')",
}


def _resolve_bucket(bucket: str, days: int) -> str:
    """bucket=auto 일 때 days에 맞춰 합리적 해상도로 선택."""
    if bucket != "auto":
        return bucket
    if   days <= 1:  return "raw"
    elif days <= 7:  return "1m"
    elif days <= 30: return "1h"
    else:            return "1d"


@app.get("/chart/{device_id}")
def get_chart(
    device_id: str,
    days: int = Query(1, ge=1, le=365, description="조회 기간 (일)"),
    metric: str = Query(
        "all",
        pattern="^(all|temp|rms)$",
        description="all=온도+RMS, temp=온도만, rms=진동만",
    ),
    bucket: str = Query(
        "auto",
        pattern="^(auto|raw|1m|5m|1h|1d)$",
        description="시간 버킷. auto=days에 맞춰 자동, raw=원본, 1m/5m/1h/1d=평균 집계",
    ),
    db: Session = Depends(get_db),
):
    """
    단일 디바이스 차트용 시계열 (column/series 형식).

    응답:
      {
        "device_id": "esp_32_0",
        "days":   7,
        "bucket": "1m",
        "metric": "all",
        "count":  10080,
        "series": {
            "t":      ["2026-05-15T08:00:00", ...],
            "temp1":  [25.3, ...],
            "temp2":  [26.1, ...],
            "rms_x":  [20, ...],
            "rms_y":  [18, ...],
            "rms_z":  [15, ...],
            "event":  ["normal", ...]
        }
      }
    """
    want_temp = metric in ("all", "temp")
    want_rms  = metric in ("all", "rms")
    resolved_bucket = _resolve_bucket(bucket, days)

    if resolved_bucket == "raw":
        rows = db.execute(text("""
            SELECT temp1, temp2, rms_x, rms_y, rms_z, event,
                   created_at AS t
            FROM temperature_log
            WHERE device_id = :device_id
              AND created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
            ORDER BY created_at ASC
        """), {"device_id": device_id, "days": days}).mappings().all()
    else:
        time_expr = _BUCKET_TIME_SQL[resolved_bucket]
        sql = f"""
            SELECT
                {time_expr}        AS t,
                ROUND(AVG(temp1),2) AS temp1,
                ROUND(AVG(temp2),2) AS temp2,
                ROUND(AVG(rms_x))   AS rms_x,
                ROUND(AVG(rms_y))   AS rms_y,
                ROUND(AVG(rms_z))   AS rms_z,
                CASE
                    WHEN SUM(event = 'disconnected') > 0 THEN 'disconnected'
                    WHEN SUM(event = 'warning')      > 0 THEN 'warning'
                    ELSE 'normal'
                END AS event
            FROM temperature_log
            WHERE device_id = :device_id
              AND created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
            GROUP BY t
            ORDER BY t ASC
        """
        rows = db.execute(text(sql), {"device_id": device_id, "days": days}).mappings().all()

    series: dict = {
        "t":     [r["t"]     for r in rows],
        "event": [r["event"] for r in rows],
    }
    if want_temp:
        series["temp1"] = [r["temp1"] for r in rows]
        series["temp2"] = [r["temp2"] for r in rows]
    if want_rms:
        series["rms_x"] = [r["rms_x"] for r in rows]
        series["rms_y"] = [r["rms_y"] for r in rows]
        series["rms_z"] = [r["rms_z"] for r in rows]

    return {
        "device_id": device_id,
        "days":      days,
        "bucket":    resolved_bucket,
        "metric":    metric,
        "count":     len(rows),
        "series":    series,
    }


# ---------- Sensor 원본 로그 (POST/PATCH/GET) ----------
@app.get("/sensor")
def get_sensor(
    device_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    if device_id:
        rows = db.execute(text("""
            SELECT id, device_id, temp1, temp2, rms_x, rms_y, rms_z, event, created_at
            FROM temperature_log
            WHERE device_id = :device_id
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"device_id": device_id, "limit": limit}).mappings().all()
    else:
        rows = db.execute(text("""
            SELECT id, device_id, temp1, temp2, rms_x, rms_y, rms_z, event, created_at
            FROM temperature_log
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return {"items": list(rows)}


@app.post("/sensor", status_code=201)
def post_sensor(payload: SensorLogIn, db: Session = Depends(get_db)):
    result = db.execute(text("""
        INSERT INTO temperature_log
            (device_id, temp1, temp2, rms_x, rms_y, rms_z, event)
        VALUES
            (:device_id, :temp1, :temp2, :rms_x, :rms_y, :rms_z, :event)
    """), payload.model_dump())
    db.commit()
    return {"ok": True, "id": result.lastrowid, "device_id": payload.device_id}


@app.patch("/sensor/{log_id}")
def update_sensor(log_id: int, payload: SensorLogUpdate, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT id FROM temperature_log WHERE id = :id"), {"id": log_id}).first()
    if not row:
        raise HTTPException(status_code=404, detail="temperature_log id not found")
    db.execute(text("""
        UPDATE temperature_log
        SET temp1 = :temp1, temp2 = :temp2,
            rms_x = :rms_x, rms_y = :rms_y, rms_z = :rms_z,
            event = :event
        WHERE id = :id
    """), {**payload.model_dump(), "id": log_id})
    db.commit()
    return {"ok": True, "id": log_id, "event": payload.event}


# ---------- Legacy aliases (기존 /temperature/* 호환) ----------
@app.get("/temperature/chart")
def legacy_temperature_chart(
    device_id: str,
    days: int = 1,
    bucket: str = Query("auto", pattern="^(auto|raw|1m|5m|1h|1d)$"),
    db: Session = Depends(get_db),
):
    """모바일 측 코드가 기존 경로를 계속 쓸 수 있도록 /chart/{device_id}로 위임."""
    return get_chart(device_id=device_id, days=days, metric="all", bucket=bucket, db=db)


@app.get("/temperature")
def legacy_temperature(
    device_id: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return get_sensor(device_id=device_id, limit=limit, db=db)


# ---------- Dummy write APIs (게이트웨이 없이 테스트) ----------
@app.post("/debug/bootstrap")
def bootstrap_minimal(db: Session = Depends(get_db)):
    """최소 더미: LN_01 → EQ_B01 → esp_32_0 ~ esp_32_9."""
    db.execute(text("""
        INSERT INTO line (line_id, line_name)
        VALUES ('LN_01', 'A조립라인')
        ON DUPLICATE KEY UPDATE line_name = VALUES(line_name)
    """))
    db.execute(text("""
        INSERT INTO equipment (equipment_id, line_id, equipment_name)
        VALUES ('EQ_B01', 'LN_01', 'B설비')
        ON DUPLICATE KEY UPDATE line_id = VALUES(line_id), equipment_name = VALUES(equipment_name)
    """))
    for suffix in range(10):
        db.execute(text("""
            INSERT INTO device (device_id, equipment_id, status, installed_on, last_seen_at)
            VALUES (:device_id, 'EQ_B01', 'Normal', CURDATE(), NOW())
            ON DUPLICATE KEY UPDATE equipment_id = VALUES(equipment_id), last_seen_at = NOW()
        """), {"device_id": f"esp_32_{suffix}"})
    db.commit()
    return {"ok": True, "devices": [f"esp_32_{i}" for i in range(10)]}


@app.post("/debug/log")
def insert_dummy_log(payload: DummyLogIn, db: Session = Depends(get_db)):
    """더미 device_log + last_seen/status 갱신."""
    device = db.execute(text("""
        SELECT device_id FROM device WHERE device_id = :device_id
    """), {"device_id": payload.device_id}).mappings().first()
    if not device:
        raise HTTPException(status_code=404, detail="device_id not found. Call /debug/bootstrap first.")

    # device.status ENUM = Normal / Warning1 / Warning2 / Warning3 / Disconnected
    if   payload.fault_grade >= 8: status = "Warning3"
    elif payload.fault_grade >= 5: status = "Warning2"
    elif payload.fault_grade >= 3: status = "Warning1"
    else:                          status = "Normal"
    db.execute(text("""
        UPDATE device
        SET last_seen_at = NOW(), status = :status
        WHERE device_id = :device_id
    """), {"device_id": payload.device_id, "status": status})

    db.execute(text("""
        INSERT INTO device_log (device_id, reboot_count, temp_out_c, temp_core_c, fault_grade, created_at)
        VALUES (:device_id, :reboot_count, :temp_out_c, :temp_core_c, :fault_grade, NOW())
    """), payload.model_dump())

    db.commit()
    return {"ok": True, "device_id": payload.device_id, "status": status, "ts": datetime.now().isoformat()}


# ---------- CSV Export (웹 다운로드 기능) ----------
@app.get("/export/sensor.csv")
def export_sensor_csv(
    device_id: Optional[str] = Query(None, description="특정 디바이스만. 미지정 시 전체"),
    limit: int = Query(1000, ge=1, le=1000, description="최대 1000개"),
    username: str = Depends(current_user),   # 로그인 필수
    db: Session = Depends(get_db),
):
    """
    Android가 GET으로 받는 것과 동일한 센서 데이터
    (ADXL335 진동 rms_x/y/z + Thermistor temp1/temp2)를
    최대 1000개, 시간 오름차순으로 CSV 다운로드.

    최신 N개를 고른 뒤 시간순(오래된→최신)으로 정렬해 반환한다.
    """
    params = {"limit": limit}
    where = ""
    if device_id:
        where = "WHERE device_id = :device_id"
        params["device_id"] = device_id

    # 최신 limit개를 뽑고(서브쿼리), 바깥에서 created_at ASC 로 시간순 정렬
    rows = db.execute(text(f"""
        SELECT created_at, device_id, temp1, temp2, rms_x, rms_y, rms_z, event
        FROM (
            SELECT created_at, device_id, temp1, temp2, rms_x, rms_y, rms_z, event
            FROM temperature_log
            {where}
            ORDER BY created_at DESC
            LIMIT :limit
        ) AS recent
        ORDER BY created_at ASC
    """), params).mappings().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["created_at", "device_id", "temp1", "temp2",
                     "rms_x", "rms_y", "rms_z", "event"])
    for r in rows:
        writer.writerow([
            r["created_at"], r["device_id"], r["temp1"], r["temp2"],
            r["rms_x"], r["rms_y"], r["rms_z"], r["event"],
        ])
    buf.seek(0)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = device_id if device_id else "all"
    fname = f"ingps_sensor_{tag}_{stamp}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------- 정적 웹 페이지 ----------
@app.get("/")
def root():
    return RedirectResponse(url="/web/login.html")

# /web/* : login.html, signup.html, index.html, style.css, app.js
app.mount("/web", StaticFiles(directory=WEB_DIR, html=True), name="web")

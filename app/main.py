import io
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from fastapi import (Depends, FastAPI, File, Header, HTTPException, Query,
                     UploadFile)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func

from . import config, emailer
from .db import (FAILED, PENDING, SCHEDULED, SENT, Professor, SessionLocal,
                 get_setting, init_db, set_setting, utcnow)
from .excel import parse_professors
from .worker import local_day_start_utc, process_due, start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sched = start_scheduler()
    yield
    sched.shutdown(wait=False)


def require_key(x_api_key: Optional[str] = Header(default=None)):
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key header")


app = FastAPI(title="Professor Mailer", lifespan=lifespan, dependencies=[Depends(require_key)])
app = FastAPI(title="Professor Mailer", lifespan=lifespan, dependencies=[Depends(require_key)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://YOUR-APP.vercel.app",   # exact Vercel URL, no trailing slash
        "http://localhost:5173",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",  # optional: Vercel preview deployments
    allow_methods=["*"],
    allow_headers=["*"],   # lets X-API-Key through
)

def get_db():
    with SessionLocal() as db:
        yield db


def to_utc_naive(dt: datetime) -> datetime:
    """Naive input is interpreted in APP_TIMEZONE; aware input is converted."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(config.APP_TIMEZONE))
    return dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def to_local_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(config.APP_TIMEZONE)).isoformat(timespec="seconds")


def serialize(p: Professor) -> dict:
    return {"id": p.id, "university": p.university, "name": p.name, "email": p.email,
            "status": p.status, "scheduled_at": to_local_iso(p.scheduled_at),
            "sent_at": to_local_iso(p.sent_at), "attempts": p.attempts,
            "error_message": p.error_message}


# ---------------------------------------------------------------- import / list
@app.post("/api/professors/import")
async def import_excel(file: UploadFile = File(...), db=Depends(get_db)):
    """Upload .xlsx with columns: University, Professor Name, Email."""
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Please upload an .xlsx file")
    try:
        valid, skipped = parse_professors(await file.read())
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read Excel file: {e}")

    existing = {e for (e,) in db.query(Professor.email).all()}
    added = 0
    for r in valid:
        if r["email"] in existing:
            skipped.append({"row": None, "reason": "already in database", "email": r["email"]})
            continue
        db.add(Professor(**r, status=PENDING))
        added += 1
    db.commit()
    return {"added": added, "skipped_count": len(skipped), "skipped": skipped[:100]}


@app.get("/api/professors")
def list_professors(status: Optional[str] = None, university: Optional[str] = None,
                    q: Optional[str] = None, page: int = Query(1, ge=1),
                    page_size: int = Query(50, ge=1, le=500), db=Depends(get_db)):
    query = db.query(Professor)
    if status:
        query = query.filter(Professor.status == status)
    if university:
        query = query.filter(Professor.university == university)
    if q:
        like = f"%{q}%"
        query = query.filter(Professor.name.ilike(like) | Professor.email.ilike(like)
                             | Professor.university.ilike(like))
    total = query.count()
    rows = query.order_by(Professor.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [serialize(r) for r in rows]}


@app.delete("/api/professors/{pid}")
def delete_professor(pid: int, db=Depends(get_db)):
    p = db.get(Professor, pid)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status == "sending":
        raise HTTPException(409, "Currently sending")
    db.delete(p)
    db.commit()
    return {"deleted": pid}


@app.get("/api/stats")
def stats(db=Depends(get_db)):
    counts = dict(db.query(Professor.status, func.count()).group_by(Professor.status).all())
    sent_today = (db.query(Professor).filter(Professor.status == SENT,
                  Professor.sent_at >= local_day_start_utc()).count())
    nxt = (db.query(func.min(Professor.scheduled_at)).filter(Professor.status == SCHEDULED).scalar())
    return {"counts": counts, "total": sum(counts.values()), "sent_today": sent_today,
            "daily_limit": config.DAILY_LIMIT, "dry_run": config.DRY_RUN,
            "next_scheduled_at": to_local_iso(nxt), "timezone": config.APP_TIMEZONE}


# ---------------------------------------------------------------- scheduling
class ScheduleRequest(BaseModel):
    start_time: datetime = Field(description="When the first email goes out. No offset = APP_TIMEZONE.")
    interval_minutes: float = Field(3, ge=0, description="Gap between emails")
    jitter_seconds: int = Field(60, ge=0, description="Random extra delay per email (looks more human)")
    per_day: Optional[int] = Field(None, ge=1, description="Max emails per day; rest roll to next day at same start time")
    ids: Optional[List[int]] = Field(None, description="Only these professors (default: all pending)")
    university: Optional[str] = None


@app.post("/api/schedule")
def schedule(req: ScheduleRequest, db=Depends(get_db)):
    start = to_utc_naive(req.start_time)
    if start < utcnow() - timedelta(minutes=1):
        raise HTTPException(400, "start_time is in the past")
    if not get_setting(db, "cv_path"):
        raise HTTPException(400, "Upload a CV first (POST /api/cv)")
    query = db.query(Professor).filter(Professor.status == PENDING)
    if req.ids:
        query = query.filter(Professor.id.in_(req.ids))
    if req.university:
        query = query.filter(Professor.university == req.university)
    rows = query.order_by(Professor.id).all()
    if not rows:
        raise HTTPException(404, "No pending professors match")

    day, in_day, offset = 0, 0, 0.0
    first = last = None
    for p in rows:
        if req.per_day and in_day >= req.per_day:
            day, in_day, offset = day + 1, 0, 0.0
        t = start + timedelta(days=day, minutes=offset, seconds=random.uniform(0, req.jitter_seconds))
        p.status, p.scheduled_at, p.error_message = SCHEDULED, t, None
        first = first or t
        last = t
        in_day += 1
        offset += req.interval_minutes
    db.commit()
    return {"scheduled": len(rows), "first_at": to_local_iso(first), "last_at": to_local_iso(last),
            "note": f"Worker also enforces DAILY_LIMIT={config.DAILY_LIMIT}"}


@app.post("/api/unschedule")
def unschedule(ids: Optional[List[int]] = None, db=Depends(get_db)):
    """Put scheduled (not yet sent) emails back to pending. Cancels them."""
    q = db.query(Professor).filter(Professor.status == SCHEDULED)
    if ids:
        q = q.filter(Professor.id.in_(ids))
    n = q.update({"status": PENDING, "scheduled_at": None}, synchronize_session=False)
    db.commit()
    return {"unscheduled": n}


@app.post("/api/retry-failed")
def retry_failed(db=Depends(get_db)):
    """Failed -> pending so you can schedule them again. Check 'Interrupted' ones in Sent folder first!"""
    n = db.query(Professor).filter(Professor.status == FAILED).update(
        {"status": PENDING, "scheduled_at": None}, synchronize_session=False)
    db.commit()
    return {"reset_to_pending": n}


@app.post("/api/run-now")
def run_now():
    """Trigger one worker pass immediately (useful for testing)."""
    process_due()
    return {"ok": True}


# ---------------------------------------------------------------- template + CV
class TemplateIn(BaseModel):
    subject: str
    body: str


@app.get("/api/template")
def get_template(db=Depends(get_db)):
    return {"subject": get_setting(db, "subject"), "body": get_setting(db, "body"),
            "placeholders": ["{name}", "{university}"], "cv": get_setting(db, "cv_path") or None}


@app.put("/api/template")
def put_template(t: TemplateIn, db=Depends(get_db)):
    set_setting(db, "subject", t.subject)
    set_setting(db, "body", t.body)
    return {"ok": True}


@app.post("/api/cv")
async def upload_cv(file: UploadFile = File(...), db=Depends(get_db)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "CV must be a .pdf")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "CV larger than 5 MB - compress it first")
    safe = "".join(c for c in file.filename if c.isalnum() or c in "._-") or "CV.pdf"
    path = config.DATA_DIR / "cv" / safe
    path.write_bytes(data)
    set_setting(db, "cv_path", str(path))
    return {"cv": str(path), "size_kb": round(len(data) / 1024, 1)}


class TestEmail(BaseModel):
    to: str


@app.post("/api/test-email")
def test_email(req: TestEmail, db=Depends(get_db)):
    """Send one real (or dry-run) email to yourself with the current template + CV."""
    try:
        msg = emailer.build_message(req.to, "Test Professor", "Test University",
                                    get_setting(db, "subject"), get_setting(db, "body"),
                                    get_setting(db, "cv_path") or None)
        emailer.send_message(msg)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e))
    return {"sent_to": req.to, "dry_run": config.DRY_RUN}
   


# ---------------------------------------------------------------- export
@app.get("/api/export")
def export_excel(db=Depends(get_db)):
    rows = [serialize(p) for p in db.query(Professor).order_by(Professor.id).all()]
    df = pd.DataFrame(rows, columns=["id", "university", "name", "email", "status",
                                     "scheduled_at", "sent_at", "attempts", "error_message"])
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Professors")
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=professors_status.xlsx"})

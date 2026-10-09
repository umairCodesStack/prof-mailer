import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, emailer
from .db import (FAILED, SCHEDULED, SENDING, SENT, Professor, SessionLocal,
                 get_setting, utcnow)

log = logging.getLogger("worker")
MAX_PER_TICK = 5


def local_day_start_utc(now_utc: datetime | None = None) -> datetime:
    """Start of today in APP_TIMEZONE, expressed as naive UTC."""
    tz = ZoneInfo(config.APP_TIMEZONE)
    now_utc = now_utc or utcnow()
    local_now = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def recover_interrupted():
    """Rows stuck in 'sending' (crash mid-send) are NOT retried automatically:
    we can't know if the mail went out, so we flag them for manual review."""
    with SessionLocal() as db:
        n = (db.query(Professor).filter(Professor.status == SENDING)
             .update({"status": FAILED,
                      "error_message": "Interrupted while sending - check your Sent folder before retrying"}))
        db.commit()
        if n:
            log.warning("%d interrupted email(s) marked failed for manual review", n)


def process_due():
    with SessionLocal() as db:
        sent_today = (db.query(Professor)
                      .filter(Professor.status == SENT, Professor.sent_at >= local_day_start_utc())
                      .count())
        subject, body = get_setting(db, "subject"), get_setting(db, "body")
        cv_path = get_setting(db, "cv_path") or None

        for _ in range(MAX_PER_TICK):
            if sent_today >= config.DAILY_LIMIT:
                log.info("Daily limit (%d) reached; waiting", config.DAILY_LIMIT)
                return
            row = (db.query(Professor)
                   .filter(Professor.status == SCHEDULED, Professor.scheduled_at <= utcnow())
                   .order_by(Professor.scheduled_at).first())
            if not row:
                return
            # Atomic claim: only one worker can flip scheduled -> sending
            claimed = (db.query(Professor)
                       .filter(Professor.id == row.id, Professor.status == SCHEDULED)
                       .update({"status": SENDING, "attempts": Professor.attempts + 1}))
            db.commit()
            if not claimed:
                continue
            try:
                msg = emailer.build_message(row.email, row.name, row.university,
                                            subject, body, cv_path)
                emailer.send_message(msg)
                row.status, row.sent_at, row.error_message = SENT, utcnow(), None
                sent_today += 1
            except Exception as e:  # noqa: BLE001
                log.exception("Failed sending to %s", row.email)
                row.status, row.error_message = FAILED, str(e)[:500]
            db.commit()


def start_scheduler() -> BackgroundScheduler:
    recover_interrupted()
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(process_due, "interval", seconds=config.CHECK_INTERVAL_SECONDS,
                  max_instances=1, coalesce=True, id="process_due")
    sched.start()
    return sched

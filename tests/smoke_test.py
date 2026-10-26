"""End-to-end test in DRY_RUN mode with a throwaway DB. Run: python tests/smoke_test.py"""
from datetime import datetime, timedelta, timezone
import io, os, sys, tempfile, time
tmp = tempfile.mkdtemp()
os.environ.update(DATA_DIR=tmp, DATABASE_URL=f"sqlite:///{tmp}/t.db", DRY_RUN="true",
                  API_KEY="k", DAILY_LIMIT="3", CHECK_INTERVAL_SECONDS="3600")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
legacy_db = sqlite3.connect(os.path.join(tmp, "t.db"))
legacy_db.executescript("""
CREATE TABLE professors (
    id INTEGER NOT NULL PRIMARY KEY,
    university VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL,
    scheduled_at DATETIME,
    sent_at DATETIME,
    attempts INTEGER NOT NULL,
    error_message TEXT,
    created_at DATETIME NOT NULL
);
CREATE UNIQUE INDEX ix_professors_email ON professors(email);
CREATE INDEX ix_professors_scheduled_at ON professors(scheduled_at);
CREATE INDEX ix_professors_status ON professors(status);
INSERT INTO professors
    (id, university, name, email, status, scheduled_at, sent_at, attempts, error_message, created_at)
VALUES
    (1, 'NJU', 'Previously contacted', 'li@nju.edu.cn', 'sent', NULL,
     '2000-01-01 00:00:00', 1, NULL, '2000-01-01 00:00:00');
""")
legacy_db.commit()
legacy_db.close()

import pandas as pd
from fastapi.testclient import TestClient
from app.emailer import build_message
from app.main import app

H = {"X-API-Key": "k"}
with TestClient(app) as c:
    assert c.get("/api/stats").status_code == 401           # auth works

    df = pd.DataFrame({"University": ["NJU", "SEU", "NJU", "SEU", "NUAA", "X"],
                       "Professor Name": ["Li", "Wang", "Zhang", "Chen", "Liu", "Bad"],
                       "Contact Address": ["li@nju.edu.cn", "wang@seu.edu.cn", "zhang@nju.edu.cn",
                                           "LI@nju.edu.cn", "liu@nuaa.edu.cn", "not-an-email"],
                       "Program": ["CS", "AI", "Robotics", "Data Science", "CS", "Bad"],
                       "Application Deadline": ["June", "July", "August", "September", "October", "Never"]})
    buf = io.BytesIO(); df.to_excel(buf, index=False); buf.seek(0)
    r = c.post("/api/professors/import", headers=H, data={"email_column": "Contact Address"},
               files={"file": ("p.xlsx", buf.read())}).json()
    print("import:", r); assert r["added"] == 5 and r["skipped_count"] == 1

    # Re-importing addresses already in the database adds new email entries.
    buf.seek(0)
    r2 = c.post("/api/professors/import", headers=H, data={"email_column": "Contact Address"},
                files={"file": ("p.xlsx", buf.getvalue())}).json()
    assert r2["added"] == 5
    assert c.get("/api/professors", headers=H, params={"q": "li@nju.edu.cn"}).json()["total"] == 5
    imported = c.get("/api/professors", headers=H,
                     params={"q": "li@nju.edu.cn", "status": "pending"}).json()["items"][0]
    assert imported["custom_fields"]["Program"] == "CS"
    assert imported["custom_fields"]["Application Deadline"] == "June"
    placeholders = c.get("/api/template", headers=H).json()["placeholders"]
    assert "{Program}" in placeholders and "{Application Deadline}" in placeholders
    dynamic_message = build_message(
        imported["email"], imported["name"], imported["university"],
        "Application for {program}", "Deadline: {Application Deadline}",
        None, imported["custom_fields"],
    )
    assert dynamic_message["Subject"] == "Application for CS"
    assert dynamic_message.get_content().strip() == "Deadline: June"

    # Scheduling and sending work without a CV attachment.
    from app.db import engine
    from sqlalchemy import inspect
    assert not any(index["unique"] and index["column_names"] == ["email"]
                   for index in inspect(engine).get_indexes("professors"))
    assert not list(build_message("me@example.com", "Test", "NJU", "Subject", "Body", None).iter_attachments())
    assert not list(build_message("me@example.com", "Test", "NJU", "Subject", "Body",
                                  os.path.join(tmp, "missing.pdf")).iter_attachments())
    start = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    r = c.post("/api/schedule", headers=H, json={"start_time": start, "interval_minutes": 0,
                                                 "jitter_seconds": 0}).json()
    assert r["scheduled"] == 10
    assert c.post("/api/unschedule", headers=H).json()["unscheduled"] == 10
    assert c.post("/api/test-email", headers=H, json={"to": "me@example.com"}).status_code == 200

    r = c.post("/api/cv", headers=H, files={"file": ("My CV.pdf", b"%PDF-1.4 fake")}); print("cv:", r.json())
    assert r.status_code == 200

    print("test-email:", c.post("/api/test-email", headers=H, json={"to": "me@example.com"}).json())

    # Schedule all imported rows, including repeated email addresses.
    start = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    r = c.post("/api/schedule", headers=H, json={"start_time": start, "interval_minutes": 0,
                                                 "jitter_seconds": 0}).json()
    print("schedule:", r); assert r["scheduled"] == 10
    assert c.get("/api/stats", headers=H).json()["counts"] == {"sent": 1, "scheduled": 10}

    time.sleep(2)
    c.put("/api/template", headers=H,
          json={"subject": "Application for {Program}",
                "body": "Deadline: {Application Deadline}"})
    from app import emailer
    sent_messages = []
    send_message = emailer.send_message
    emailer.send_message = sent_messages.append
    try:
        c.post("/api/run-now", headers=H)
    finally:
        emailer.send_message = send_message
    assert len(sent_messages) == 3
    assert any(msg["Subject"] == "Application for CS"
               and msg.get_body(preferencelist=("plain",)).get_content().strip() == "Deadline: June"
               for msg in sent_messages)
    s = c.get("/api/stats", headers=H).json(); print("after run:", s)
    assert s["counts"] == {"sent": 4, "scheduled": 7}        # DAILY_LIMIT=3 enforced
    c.post("/api/run-now", headers=H)
    assert c.get("/api/stats", headers=H).json()["counts"] == {"sent": 4, "scheduled": 7}

    # unschedule + export
    assert c.post("/api/unschedule", headers=H).json()["unscheduled"] == 7
    x = c.get("/api/export", headers=H)
    out = pd.read_excel(io.BytesIO(x.content)); print(out[["name", "status"]])
    assert list(out.status).count("sent") == 4
print("ALL OK")

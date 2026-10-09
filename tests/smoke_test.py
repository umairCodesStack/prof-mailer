"""End-to-end test in DRY_RUN mode with a throwaway DB. Run: python tests/smoke_test.py"""
import io, os, sys, tempfile, time
tmp = tempfile.mkdtemp()
os.environ.update(DATA_DIR=tmp, DATABASE_URL=f"sqlite:///{tmp}/t.db", DRY_RUN="true",
                  API_KEY="k", DAILY_LIMIT="3", CHECK_INTERVAL_SECONDS="3600")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from fastapi.testclient import TestClient
from app.main import app

H = {"X-API-Key": "k"}
with TestClient(app) as c:
    assert c.get("/api/stats").status_code == 401           # auth works

    df = pd.DataFrame({"University": ["NJU", "SEU", "NJU", "SEU", "NUAA", "X"],
                       "Professor Name": ["Li", "Wang", "Zhang", "Chen", "Liu", "Bad"],
                       "Email": ["li@nju.edu.cn", "wang@seu.edu.cn", "zhang@nju.edu.cn",
                                 "LI@nju.edu.cn", "liu@nuaa.edu.cn", "not-an-email"]})
    buf = io.BytesIO(); df.to_excel(buf, index=False); buf.seek(0)
    r = c.post("/api/professors/import", headers=H, files={"file": ("p.xlsx", buf.read())}).json()
    print("import:", r); assert r["added"] == 4 and r["skipped_count"] == 2

    # re-import adds nothing
    buf.seek(0)
    r2 = c.post("/api/professors/import", headers=H, files={"file": ("p.xlsx", buf.getvalue())}).json()
    assert r2["added"] == 0

    # scheduling requires a CV
    assert c.post("/api/schedule", headers=H, json={"start_time": "2099-01-01T09:00:00"}).status_code == 400
    r = c.post("/api/cv", headers=H, files={"file": ("My CV.pdf", b"%PDF-1.4 fake")}); print("cv:", r.json())
    assert r.status_code == 200

    print("test-email:", c.post("/api/test-email", headers=H, json={"to": "me@example.com"}).json())

    # schedule 4 emails for 'now', no spacing
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    r = c.post("/api/schedule", headers=H, json={"start_time": start, "interval_minutes": 0,
                                                 "jitter_seconds": 0}).json()
    print("schedule:", r); assert r["scheduled"] == 4
    assert c.get("/api/stats", headers=H).json()["counts"] == {"scheduled": 4}

    time.sleep(2)
    c.post("/api/run-now", headers=H)
    s = c.get("/api/stats", headers=H).json(); print("after run:", s)
    assert s["counts"] == {"sent": 3, "scheduled": 1}        # DAILY_LIMIT=3 enforced
    c.post("/api/run-now", headers=H)
    assert c.get("/api/stats", headers=H).json()["counts"] == {"sent": 3, "scheduled": 1}

    # unschedule + export
    assert c.post("/api/unschedule", headers=H).json()["unscheduled"] == 1
    x = c.get("/api/export", headers=H)
    out = pd.read_excel(io.BytesIO(x.content)); print(out[["name", "status"]])
    assert list(out.status).count("sent") == 3
print("ALL OK")

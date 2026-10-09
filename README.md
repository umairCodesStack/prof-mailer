# Professor Mailer (backend)

FastAPI + SQLite + APScheduler. Import an Excel sheet of professors, schedule emails with your CV, track pending / scheduled / sent / failed.

## Run

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then edit it
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for the interactive API (click "Authorize"-style: add header `X-API-Key`).
`DRY_RUN=true` (default) never sends anything, it only logs. Set `DRY_RUN=false` when ready.

## Workflow

1. `PUT  /api/template` subject + body, use `{name}` and `{university}`
2. `POST /api/cv` upload your CV (.pdf, max 5 MB)
3. `POST /api/professors/import` Excel with columns: University, Professor Name, Email
4. `POST /api/test-email` send one to yourself first
5. `POST /api/schedule` e.g. `{"start_time":"2026-10-10T09:00:00","interval_minutes":4,"jitter_seconds":90,"per_day":30}`
6. `GET  /api/stats`, `GET /api/professors?status=sent`, `GET /api/export` (Excel with statuses)

Other: `POST /api/unschedule`, `POST /api/retry-failed`, `DELETE /api/professors/{id}`.

## Safety built in

- Unique email (no duplicates), atomic claim before sending (no double-send).
- If the app crashes mid-send, that row becomes `failed - Interrupted`; check your Sent folder before retrying.
- `DAILY_LIMIT` enforced by the worker, even if you schedule more.
- Times without an offset use `APP_TIMEZONE`.

Test: `python tests/smoke_test.py`

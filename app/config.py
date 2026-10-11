import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

#chnages
def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "cv").mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/mailer.db")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_NAME = os.getenv("FROM_NAME", "")

DRY_RUN = _bool("DRY_RUN", False)  # safe by default
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "30"))
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Karachi")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "30"))
API_KEY = os.getenv("API_KEY", "")

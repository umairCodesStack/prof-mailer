from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Integer, String, Text, create_engine,
                        event)
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

engine = create_engine(
    config.DATABASE_URL, connect_args={"check_same_thread": False}
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()

# Status values
PENDING, SCHEDULED, SENDING, SENT, FAILED = (
    "pending", "scheduled", "sending", "sent", "failed",
)


def utcnow() -> datetime:
    """Naive UTC datetime (all DB datetimes are naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Professor(Base):
    __tablename__ = "professors"

    id = Column(Integer, primary_key=True)
    university = Column(String(255), nullable=False, default="")
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default=PENDING, index=True)
    scheduled_at = Column(DateTime, nullable=True, index=True)  # UTC
    sent_at = Column(DateTime, nullable=True)  # UTC
    attempts = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Setting(Base):
    """Simple key/value store: subject, body, cv_path."""
    __tablename__ = "settings"
    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=False, default="")


DEFAULT_SUBJECT = "Master's Application – Request for Acceptance Letter"
DEFAULT_BODY = """Dear Professor {name},

I hope this message finds you well. My name is Muhammad Umair Ashraf, and I am writing to express my interest in pursuing a master's degree at {university} under your supervision.

My academic and professional background is in software engineering, with experience in areas including software development, machine learning, and related technologies. I have been a good and consistent student throughout my academic journey.I'm highly motivated to do masters under your supervision and make great contribution in the field of computer science.

I have attached my CV for your consideration. I would be grateful for the opportunity to discuss this further.

Thank you for your time.

Best regards,
Muhammad Umair Ashraf
"""


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for k, v in (("subject", DEFAULT_SUBJECT), ("body", DEFAULT_BODY), ("cv_path", "")):
            if not db.get(Setting, k):
                db.add(Setting(key=k, value=v))
        db.commit()


def get_setting(db, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row else ""


def set_setting(db, key: str, value: str):
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()

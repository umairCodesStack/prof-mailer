from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Text,
                        create_engine, event, inspect)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.schema import CreateTable

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
    email = Column(String(255), nullable=False, index=True)
    custom_fields = Column(Text, nullable=False, default="{}", server_default="{}")
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

I would be grateful for the opportunity to discuss this further.

Thank you for your time.

Best regards,
Muhammad Umair Ashraf
"""


def init_db():
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("professors")}
        if "custom_fields" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE professors ADD COLUMN custom_fields TEXT NOT NULL DEFAULT '{}'"
            )
        unique_email_indexes = [
            index for index in inspect(conn).get_indexes("professors")
            if index["unique"] and index["column_names"] == ["email"]
        ]
        if unique_email_indexes:
            temporary_table = Professor.__table__.to_metadata(
                MetaData(), name="professors_without_unique_email"
            )
            conn.execute(CreateTable(temporary_table))
            columns = [column.name for column in Professor.__table__.columns]
            conn.execute(temporary_table.insert().from_select(
                columns, Professor.__table__.select()
            ))
            Professor.__table__.drop(conn)
            preparer = conn.dialect.identifier_preparer
            conn.exec_driver_sql(
                f"ALTER TABLE {preparer.quote(temporary_table.name)} "
                f"RENAME TO {preparer.quote(Professor.__tablename__)}"
            )
            for index in Professor.__table__.indexes:
                index.create(conn, checkfirst=True)
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

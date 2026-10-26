import logging
import mimetypes
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Mapping

from . import config

log = logging.getLogger("mailer")


def render(template: str, name: str, university: str,
           fields: Mapping[str, str] | None = None) -> str:
    values = {"name": name, "university": university}
    values.update({key.strip().lower(): value for key, value in (fields or {}).items()})
    return re.sub(
        r"\{([^{}]+)\}",
        lambda match: values.get(match.group(1).strip().lower(), match.group(0)),
        template,
    )


def build_message(to_email: str, name: str, university: str,
                  subject: str, body: str, cv_path: str | None,
                  fields: Mapping[str, str] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((config.FROM_NAME, config.SMTP_USER)) if config.SMTP_USER else "dry-run@localhost"
    msg["To"] = to_email
    msg["Subject"] = render(subject, name, university, fields)
    msg["Message-ID"] = make_msgid()
    msg.set_content(render(body, name, university, fields))
    if cv_path:
        p = Path(cv_path)
        if not p.is_file():
            log.warning("CV file not found; sending without attachment: %s", cv_path)
        else:
            ctype, _ = mimetypes.guess_type(p.name)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    return msg


def send_message(msg: EmailMessage) -> None:
    if config.DRY_RUN:
        log.info("[DRY RUN] would send to=%s subject=%r attachments=%d",
                 msg["To"], msg["Subject"], len(list(msg.iter_attachments())))
        return
    if not (config.SMTP_USER and config.SMTP_PASSWORD):
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD are not configured")
    ctx = ssl.create_default_context()
    if config.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx, timeout=30) as s:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)

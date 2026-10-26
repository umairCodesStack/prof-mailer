import io
import re
from typing import List, Tuple

import pandas as pd

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Accepted header names (lower-cased, stripped)
ALIASES = {
    "university": ["university", "university name", "school", "institution", "学校", "高校"],
    "name": ["professor name", "professor", "name", "supervisor", "姓名", "教师"],
    "email": ["email", "e-mail", "email address", "mail", "邮箱", "电子邮箱", "电子邮件"],
}


def _find_columns(columns, email_column: str | None = None) -> dict:
    lowered = {}
    for column in columns:
        key = str(column).strip().lower()
        if not key:
            raise ValueError("Excel column headers must not be empty")
        if key in lowered:
            raise ValueError(f"Duplicate Excel column header: {column}")
        lowered[key] = column

    found = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in lowered:
                found[field] = lowered[n]
                break
    if email_column:
        selected = lowered.get(email_column.strip().lower())
        if selected is None:
            raise ValueError(
                f"Email column '{email_column}' was not found. "
                f"Available columns: {', '.join(map(str, columns))}"
            )
        found["email"] = selected
    if "email" not in found:
        raise ValueError(
            "An email column is required. Use a recognized email header or specify "
            f"email_column. Available columns: {', '.join(map(str, columns))}"
        )
    return found


def parse_professors(file_bytes: bytes, email_column: str | None = None) -> Tuple[List[dict], List[dict]]:
    """Return (valid_rows, skipped_rows). Excel row numbers start at 2 (row 1 = header)."""
    df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
    cols = _find_columns(df.columns, email_column)
    valid, skipped = [], []
    for i, rec in enumerate(df.to_dict("records"), start=2):
        fields = {
            str(column).strip(): (str(rec[column]).strip() if pd.notna(rec[column]) else "")
            for column in df.columns
        }
        email = fields[str(cols["email"]).strip()].lower()
        if not email:
            skipped.append({"row": i, "reason": "missing email", "email": email})
        elif not EMAIL_RE.match(email):
            skipped.append({"row": i, "reason": "invalid email", "email": email})
        else:
            valid.append({
                "university": fields.get(str(cols.get("university", "")).strip(), ""),
                "name": fields.get(str(cols.get("name", "")).strip(), ""),
                "email": email,
                "custom_fields": fields,
            })
    return valid, skipped

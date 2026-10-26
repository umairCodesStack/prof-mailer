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


def _find_columns(columns) -> dict:
    lowered = {str(c).strip().lower(): c for c in columns}
    found = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in lowered:
                found[field] = lowered[n]
                break
    missing = [f for f in ALIASES if f not in found]
    if missing:
        raise ValueError(
            f"Missing column(s): {', '.join(missing)}. "
            f"Found headers: {', '.join(map(str, columns))}"
        )
    return found


def parse_professors(file_bytes: bytes) -> Tuple[List[dict], List[dict]]:
    """Return (valid_rows, skipped_rows). Excel row numbers start at 2 (row 1 = header)."""
    df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
    cols = _find_columns(df.columns)
    valid, skipped = [], []
    for i, rec in enumerate(df.to_dict("records"), start=2):
        uni = (rec.get(cols["university"]) or "").strip() if isinstance(rec.get(cols["university"]), str) else ""
        name = (rec.get(cols["name"]) or "").strip() if isinstance(rec.get(cols["name"]), str) else ""
        email = (rec.get(cols["email"]) or "").strip().lower() if isinstance(rec.get(cols["email"]), str) else ""
        if not name or not email:
            skipped.append({"row": i, "reason": "missing name or email", "email": email})
        elif not EMAIL_RE.match(email):
            skipped.append({"row": i, "reason": "invalid email", "email": email})
        else:
            valid.append({"university": uni, "name": name, "email": email})
    return valid, skipped

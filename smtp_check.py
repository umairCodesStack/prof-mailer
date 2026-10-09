import smtplib, ssl, os
from dotenv import load_dotenv
load_dotenv()

host = os.getenv("SMTP_HOST"); port = int(os.getenv("SMTP_PORT", "587"))
user = os.getenv("SMTP_USER"); pw = os.getenv("SMTP_PASSWORD")
print("host:", host, "port:", port)
print("user:", repr(user))
print("password length:", len(pw or ""), "(should be 16)")
print("dry run:", os.getenv("DRY_RUN"))

try:
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=20)
    else:
        s = smtplib.SMTP(host, port, timeout=20); s.starttls(context=ctx)
    s.login(user, pw)
    print("LOGIN OK")
    s.quit()
except Exception as e:
    print("ERROR:", type(e).__name__, e)
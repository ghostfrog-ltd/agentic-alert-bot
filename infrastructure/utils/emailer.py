import os, smtplib, socket
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("ALERT_SMTP_HOST", "").strip()     # e.g. smtp.zoho.eu
SMTP_PORT = int(os.getenv("ALERT_SMTP_PORT", "587"))     # 587 for STARTTLS
SMTP_USER = os.getenv("ALERT_SMTP_USER", "").strip()
SMTP_PASS = os.getenv("ALERT_SMTP_PASS", "").strip()
FROM_ADDR = os.getenv("ALERT_FROM", SMTP_USER or "info@ghostfrog.co.uk").strip()
TO_ADDR   = os.getenv("ALERT_TO", "info@ghostfrog.co.uk").strip()
TIMEOUT_S = 15

if not SMTP_HOST:
    raise RuntimeError("ALERT_SMTP_HOST not set (e.g. smtp.zoho.eu)")

def _send_via_starttls(msg):
    # Force IPv4 to dodge occasional IPv6 issues
    try:
        ipv4 = socket.getaddrinfo(SMTP_HOST, SMTP_PORT, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except Exception:
        ipv4 = None

    host_for_connect = ipv4 or SMTP_HOST
    with smtplib.SMTP(host_for_connect, SMTP_PORT, timeout=TIMEOUT_S) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        if SMTP_USER and SMTP_PASS:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def _send_via_ssl(msg):
    port = 465
    # Same IPv4 trick
    try:
        ipv4 = socket.getaddrinfo(SMTP_HOST, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except Exception:
        ipv4 = None
    host_for_connect = ipv4 or SMTP_HOST

    with smtplib.SMTP_SSL(host_for_connect, port, timeout=TIMEOUT_S) as s:
        s.ehlo()
        if SMTP_USER and SMTP_PASS:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def send_email(subject: str, body: str, to_addr: str | None = None):
    recipient = (to_addr or TO_ADDR).strip()
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = FROM_ADDR
    msg["To"] = recipient

    # Try STARTTLS first (587), then SSL (465)
    try:
        _send_via_starttls(msg)
        return
    except Exception as e1:
        # Fallback to SMTPS (465)
        try:
            _send_via_ssl(msg)
            return
        except Exception as e2:
            raise RuntimeError(
                f"Email send failed. STARTTLS error: {e1}; SSL fallback error: {e2}"
            )

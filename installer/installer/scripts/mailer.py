import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Dict


def send_email(subject: str, body: str) -> Dict[str, object]:
    """
    Sends an email using SMTP configuration from environment variables.
    Returns: {"ok": True} or {"ok": False, "error": "..."}
    """

    enabled = os.environ.get("CHRONOVAULT_SMTP_ENABLED", "false").lower() == "true"
    if not enabled:
        return {"ok": False, "error": "SMTP disabled"}

    host = os.environ.get("CHRONOVAULT_SMTP_HOST")
    port = int(os.environ.get("CHRONOVAULT_SMTP_PORT", "587"))
    username = os.environ.get("CHRONOVAULT_SMTP_USERNAME")
    password = os.environ.get("CHRONOVAULT_SMTP_PASSWORD")
    use_tls = os.environ.get("CHRONOVAULT_SMTP_USE_TLS", "true").lower() == "true"
    sender = os.environ.get("CHRONOVAULT_SMTP_FROM")
    recipient = os.environ.get("CHRONOVAULT_SMTP_TO")

    if not all([host, port, username, password, sender, recipient]):
        return {"ok": False, "error": "Missing SMTP configuration variables"}

    try:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)

        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port) as server:
                server.starttls(context=context)
                server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as server:
                server.login(username, password)
                server.send_message(msg)

        return {"ok": True}

    except Exception as e:
        return {"ok": False, "error": str(e)}


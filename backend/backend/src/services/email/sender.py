"""Transactional email through Brevo's SMTP relay (same account as sign-up codes).

Sending is best effort: a mail failure is logged and never undoes or blocks the action
that triggered it. Without SMTP credentials nothing is sent.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from src.config.manager import settings

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(
        settings.SMTP_LOGIN and settings.SMTP_PASSWORD and settings.EMAIL_FROM_ADDRESS
    )


def send_email(to: str, subject: str, html: str, text: str) -> bool:
    if not to or not configured():
        logger.info("email_skipped", extra={"subject": subject, "configured": configured()})
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.EMAIL_FROM_NAME, settings.EMAIL_FROM_ADDRESS))
    message["To"] = to
    message["Message-ID"] = make_msgid(domain="barabaricollective.org")
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    try:
        if settings.SMTP_PORT == 465:
            client = smtplib.SMTP_SSL(
                settings.SMTP_HOST, 465, context=ssl.create_default_context(), timeout=15
            )
        else:
            # Brevo's relay uses STARTTLS on 587.
            client = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
            client.starttls(context=ssl.create_default_context())
        with client:
            client.login(settings.SMTP_LOGIN, settings.SMTP_PASSWORD)
            client.send_message(message)
        return True
    except Exception:
        logger.exception("email_send_failed", extra={"subject": subject})
        return False

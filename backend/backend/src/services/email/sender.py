from __future__ import annotations

"""Transactional email through Brevo's SMTP relay or AWS SES.

Sending is best effort: a mail failure is logged and never undoes or blocks the action
that triggered it. Without SMTP or AWS SES credentials nothing is sent.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from src.config.manager import settings

logger = logging.getLogger(__name__)


def ses_configured() -> bool:
    aws_key = getattr(settings, "AWS_ACCESS_KEY_ID", None)
    aws_secret = getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
    from_addr = getattr(settings, "EMAIL_FROM_ADDRESS", None)
    return bool(aws_key and aws_secret and from_addr)


def configured() -> bool:
    smtp_ok = bool(
        settings.SMTP_LOGIN and settings.SMTP_PASSWORD and settings.EMAIL_FROM_ADDRESS
    )
    return smtp_ok or ses_configured()


def _send_via_ses(to: str, subject: str, html: str, text: str) -> bool:
    try:
        import boto3
        region = getattr(settings, "AWS_REGION", "ap-south-1") or "ap-south-1"
        client = boto3.client(
            "ses",
            region_name=region,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        sender_header = formataddr((settings.EMAIL_FROM_NAME, settings.EMAIL_FROM_ADDRESS))
        client.send_email(
            Source=sender_header,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text, "Charset": "UTF-8"},
                    "Html": {"Data": html, "Charset": "UTF-8"},
                },
            },
        )
        logger.info("email_sent_via_ses", extra={"to": to, "subject": subject})
        return True
    except Exception:
        logger.exception("ses_send_failed", extra={"subject": subject})
        return False


def send_email(to: str, subject: str, html: str, text: str) -> bool:
    if not to or not configured():
        logger.info("email_skipped", extra={"subject": subject, "configured": configured()})
        return False

    # Attempt SMTP first if SMTP credentials are provided
    smtp_ok = bool(settings.SMTP_LOGIN and settings.SMTP_PASSWORD and settings.EMAIL_FROM_ADDRESS)
    if smtp_ok:
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
                client = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
                client.starttls(context=ssl.create_default_context())
            with client:
                client.login(settings.SMTP_LOGIN, settings.SMTP_PASSWORD)
                client.send_message(message)
            return True
        except Exception:
            logger.exception("smtp_send_failed_trying_ses_if_available", extra={"subject": subject})

    # Fallback or primary to AWS SES
    if ses_configured():
        return _send_via_ses(to, subject, html, text)

    return False


"""A short reminder to practise, in the same plain brand as the interview emails."""

from html import escape

from src.services.email.human_interview_emails import BLUE, FONT, INK, LINE, MUTED, NAVY, YELLOW


def build(payload: dict) -> tuple[str, str, str]:
    name = (payload.get("student_name") or "there").split()[0]
    message = payload.get("message") or "A short practice today keeps you on track."
    link = payload.get("link") or ""
    subject = "Your next practice is waiting"
    button = (
        f'<a href="{escape(link, quote=True)}" style="display:inline-block;background:{YELLOW};color:#111111;'
        f'font-weight:700;text-decoration:none;padding:12px 22px;border-radius:10px">Start practising</a>'
        if link
        else ""
    )
    html = (
        f'<div style="font-family:{FONT};color:{INK};max-width:520px;margin:0 auto;padding:24px;'
        f'border:1px solid {LINE};border-radius:16px">'
        f'<h1 style="color:{NAVY};font-size:22px;margin:0 0 12px">{escape(subject)}</h1>'
        f'<p style="margin:0 0 8px">Hi {escape(name)},</p>'
        f'<p style="margin:0 0 20px;color:{MUTED}">{escape(message)}</p>{button}'
        f'<p style="margin:20px 0 0;font-size:13px;color:{BLUE}">Samvaad Saathi</p></div>'
    )
    text = f"Hi {name},\n\n{message}\n\n{link}\n"
    return subject, html, text

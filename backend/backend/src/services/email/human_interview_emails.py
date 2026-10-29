"""Branded emails to students about human interview rounds.

Brand: white canvas, navy #1F285B, blue #344499 for links, yellow #FFCC33 only as a thin
accent and as the button fill with black text (never yellow text, never white on yellow), plain calm language.
Built with tables and inline styles so it renders in Gmail, Outlook and phone mail apps.
"""

import datetime as dt
from html import escape
from zoneinfo import ZoneInfo

from src.config.manager import settings

IST = ZoneInfo("Asia/Kolkata")
NAVY, BLUE, YELLOW, INK, MUTED, LINE, SOFT = (
    "#1F285B",
    "#344499",
    "#FFCC33",
    "#111827",
    "#4B5563",
    "#DEE8FB",
    "#F5F9FF",
)
FONT = "Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

RESULT_WORDS = {
    "selected": "Selected",
    "next_round": "You move to the next round",
    "on_hold": "On hold",
    "not_selected": "Not selected this time",
}


def when(starts_at: dt.datetime, ends_at: dt.datetime) -> tuple[str, str]:
    start, end = starts_at.astimezone(IST), ends_at.astimezone(IST)
    minutes = round((end - start).total_seconds() / 60)
    day = start.strftime("%A, %-d %B %Y")
    time = f"{start.strftime('%-I:%M %p')} IST · {minutes} minutes"
    return day, time


def _rows(details: list[tuple[str, str]]) -> str:
    return "".join(
        f"""<tr>
<td style="padding:10px 0;border-bottom:1px solid {LINE};font:14px/20px {FONT};color:{MUTED};width:38%;vertical-align:top">{escape(label)}</td>
<td style="padding:10px 0;border-bottom:1px solid {LINE};font:600 15px/21px {FONT};color:{INK};vertical-align:top">{escape(value)}</td>
</tr>"""
        for label, value in details
        if value
    )


def render(
    *,
    preheader: str,
    eyebrow: str,
    heading: str,
    greeting_name: str,
    paragraphs: list[str],
    details: list[tuple[str, str]],
    note_title: str | None = None,
    note: str | None = None,
    button: str = "Open Samvaad Saathi",
) -> tuple[str, str]:
    """Return (html, text). Every value is escaped; callers pass plain text."""
    url = settings.STUDENT_APP_URL.rstrip("/") + "/human-interviews"
    body = "".join(
        f'<p style="margin:0 0 14px;font:16px/24px {FONT};color:{INK}">{escape(p)}</p>'
        for p in paragraphs
    )
    note_html = (
        f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0 0;background:{SOFT};border-radius:12px">
<tr><td style="padding:16px 18px">
<p style="margin:0 0 6px;font:600 15px/21px {FONT};color:{NAVY}">{escape(note_title or "")}</p>
<p style="margin:0;font:15px/22px {FONT};color:{INK};white-space:pre-line">{escape(note)}</p>
</td></tr></table>"""
        if note
        else ""
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>{escape(heading)}</title></head>
<body style="margin:0;padding:0;background:{SOFT}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SOFT}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#FFFFFF;border-radius:16px;overflow:hidden;border:1px solid {LINE}">
<tr><td style="background:{NAVY};padding:22px 28px">
<p style="margin:0;font:800 18px/24px {FONT};color:#FFFFFF;letter-spacing:-0.2px">The Barabari Collective</p>
<p style="margin:2px 0 0;font:14px/20px {FONT};color:#DEE8FB">Samvaad Saathi</p>
</td></tr>
<tr><td style="height:4px;background:{YELLOW};line-height:4px;font-size:0">&nbsp;</td></tr>
<tr><td style="padding:28px 28px 8px">
<p style="margin:0 0 8px;font:700 12px/16px {FONT};letter-spacing:1.5px;text-transform:uppercase;color:{BLUE}">{escape(eyebrow)}</p>
<h1 style="margin:0 0 18px;font:700 26px/32px Poppins,{FONT};color:{NAVY}">{escape(heading)}</h1>
<p style="margin:0 0 14px;font:16px/24px {FONT};color:{INK}">Hi {escape(greeting_name or "there")},</p>
{body}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 0">{_rows(details)}</table>
{note_html}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:26px 0 8px"><tr>
<td style="background:{YELLOW};border-radius:10px">
<a href="{escape(url)}" style="display:inline-block;padding:14px 26px;font:700 16px/20px {FONT};color:#000000;text-decoration:none">{escape(button)}</a>
</td></tr></table>
<p style="margin:12px 0 24px;font:13px/19px {FONT};color:{MUTED}">The Join button on that page opens 15 minutes before the start. If the button above does not work, copy this address into your browser: <a href="{escape(url)}" style="color:{BLUE}">{escape(url)}</a></p>
</td></tr>
<tr><td style="padding:18px 28px;border-top:1px solid {LINE}">
<p style="margin:0;font:13px/19px {FONT};color:{MUTED}">Questions? Reply to your program team. This email was sent because an interview was set up for you on Samvaad Saathi.</p>
<p style="margin:8px 0 0;font:12px/18px {FONT};color:{MUTED}">© {dt.date.today().year} The Barabari Collective</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""
    text_lines = [
        "The Barabari Collective · Samvaad Saathi",
        "",
        heading,
        "",
        f"Hi {greeting_name or 'there'},",
        "",
        *paragraphs,
        "",
        *[f"{label}: {value}" for label, value in details if value],
    ]
    if note:
        text_lines += ["", f"{note_title}:", note]
    text_lines += ["", f"{button}: {url}", "", "The Join button opens 15 minutes before the start."]
    return html, "\n".join(text_lines)


def build(event: str, data: dict) -> tuple[str, str, str]:
    """Return (subject, html, text) for one interview event."""
    day, time = when(data["starts_at"], data["ends_at"])
    round_line = data["title"] + (f" · {data['role_name']}" if data.get("role_name") else "")
    details = [
        ("Round", round_line),
        ("Date", day),
        ("Time", time),
        ("Interviewer", data.get("interviewer_name") or "Your interviewer"),
        ("Where", "Google Meet (link on your Samvaad page)"),
    ]
    name = data.get("student_name") or ""
    if event == "scheduled":
        subject = f"Interview scheduled: {data['title']} on {day}"
        html, text = render(
            preheader=f"{day}, {time}. Join from Samvaad Saathi.",
            eyebrow="Interview scheduled",
            heading="You have an interview",
            greeting_name=name,
            paragraphs=[
                "A live interview with a person has been set up for you. Here are the details.",
                "On the day, open Samvaad Saathi and press Join. Use a quiet place, and check your camera and microphone a few minutes early.",
            ],
            details=details,
            note_title="What to prepare",
            note=data.get("instructions"),
            button="See interview and join",
        )
    elif event == "rescheduled":
        subject = f"New time for your interview: {data['title']}"
        html, text = render(
            preheader=f"Your interview has moved to {day}, {time}.",
            eyebrow="Time changed",
            heading="Your interview has a new time",
            greeting_name=name,
            paragraphs=[
                "The time of your interview has changed. Please use the new date and time below. The old time no longer applies.",
            ],
            details=details,
            note_title="What to prepare",
            note=data.get("instructions"),
            button="See the new time",
        )
    elif event == "cancelled":
        subject = f"Interview cancelled: {data['title']}"
        html, text = render(
            preheader="This interview will not take place at the planned time.",
            eyebrow="Interview cancelled",
            heading="Your interview was cancelled",
            greeting_name=name,
            paragraphs=[
                "This interview will not take place. You do not need to join.",
                "Your program team will tell you if a new time is set. You can keep practising on Samvaad Saathi meanwhile.",
            ],
            details=details[:3],
            button="Open Samvaad Saathi",
        )
    elif event == "result":
        word = RESULT_WORDS.get(data.get("result") or "", "Result available")
        subject = f"Your interview result: {data['title']}"
        html, text = render(
            preheader=f"Result: {word}.",
            eyebrow="Interview result",
            heading="Your interview result is ready",
            greeting_name=name,
            paragraphs=["Thank you for taking the interview. Here is your result."],
            details=[("Round", round_line), ("Date", day), ("Result", word)],
            note_title="Feedback from your interviewer",
            note=data.get("feedback"),
            button="See your result",
        )
    else:
        raise ValueError(f"Unknown interview email event: {event}")
    return subject, html, text

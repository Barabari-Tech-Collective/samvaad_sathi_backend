"""Email rendering for Bulk ATS student feedback and corporate placement pitches.

Follows Barabari branding:
- Navy: #1F285B, Link Blue: #344499, Accent: #FFCC33, Text: #111827, Muted: #4B5563
- Table-based inline styles for Gmail, Outlook, and mobile clients.
- Produces both multipart HTML and clean plain-text alternatives.
"""

from html import escape

NAVY = "#1F285B"
BLUE = "#344499"
YELLOW = "#FFCC33"
INK = "#111827"
MUTED = "#4B5563"
LINE = "#DEE8FB"
SOFT = "#F5F9FF"
WARNING_BG = "#FFFBEB"
WARNING_BORDER = "#FDE68A"
WARNING_TEXT = "#92400E"
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def build_student_feedback_email(
    *,
    candidate_name: str,
    role_name: str,
    missing_skills: list[str] | None = None,
    broken_links: list[dict] | None = None,
    recommendations: list[str] | None = None,
    strengths: list[str] | None = None,
    mentor_notes: str | None = None,
) -> tuple[str, str, str]:
    """Build personalized improvement feedback email for an unselected or developing student.

    Returns:
        (subject, html_content, text_content)
    """
    safe_name = (candidate_name or "Candidate").strip()
    safe_role = (role_name or "the role").strip()
    missing = [s.strip() for s in (missing_skills or []) if s and s.strip()]
    recs = [r.strip() for r in (recommendations or []) if r and r.strip()]
    strong = [st.strip() for st in (strengths or []) if st and st.strip()]
    links = broken_links or []

    subject = f"Your Application Feedback & Recommendations for {safe_role}"

    # Plain text version
    text_lines = [
        f"Hi {safe_name},",
        "",
        f"Thank you for participating in the screening process for {safe_role}.",
        "We reviewed your profile carefully and wanted to share constructive, actionable feedback to help you strengthen your resume and technical readiness for upcoming opportunities.",
        "",
    ]
    if strong:
        text_lines.append("Key Strengths Observed:")
        for item in strong:
            text_lines.append(f" - {item}")
        text_lines.append("")

    if missing:
        text_lines.append(f"Identified Skill Gaps for {safe_role}:")
        for skill in missing:
            text_lines.append(f" - {skill}")
        text_lines.append("")

    if links:
        text_lines.append("Important Resume Link Notices:")
        for link in links:
            url = link.get("url", "Link")
            reason = link.get("reason") or "Inaccessible / 404"
            text_lines.append(f" - {url} ({reason})")
        text_lines.append("We recommend verifying all project and portfolio URLs on your resume so employers can view your work.")
        text_lines.append("")

    if mentor_notes:
        text_lines.append(f"Mentor Notes:\n{mentor_notes}\n")

    if recs:
        text_lines.append("Recommended Next Steps to Improve:")
        for step in recs:
            text_lines.append(f" - {step}")
        text_lines.append("")

    text_lines.append("Keep learning and refining your projects. We look forward to seeing your progress!")
    text_lines.append("Best regards,\nThe Placement & Academic Team")
    text_content = "\n".join(text_lines)

    # HTML rendering
    strengths_html = ""
    if strong:
        items = "".join(f"<li style='margin-bottom:6px;'>{escape(item)}</li>" for item in strong)
        strengths_html = f"""
        <div style="margin-top:20px;padding:16px;background-color:{SOFT};border-radius:8px;border-left:4px solid {BLUE};">
            <h3 style="margin:0 0 8px 0;font-size:15px;color:{NAVY};font-family:{FONT};">Key Strengths Identified</h3>
            <ul style="margin:0;padding-left:20px;color:{INK};font-size:14px;line-height:22px;font-family:{FONT};">{items}</ul>
        </div>
        """

    skills_html = ""
    if missing:
        items = "".join(f"<span style='display:inline-block;background:#EBF3FF;color:{BLUE};padding:4px 10px;border-radius:14px;font-size:13px;font-weight:600;margin:4px 4px 4px 0;'>{escape(skill)}</span>" for skill in missing)
        skills_html = f"""
        <div style="margin-top:20px;">
            <h3 style="margin:0 0 8px 0;font-size:15px;color:{NAVY};font-family:{FONT};">Skill Gaps to Focus On for {escape(safe_role)}</h3>
            <p style="margin:0 0 10px 0;font-size:14px;color:{MUTED};font-family:{FONT};">
                The role rubric prioritized experience in the following areas:
            </p>
            <div>{items}</div>
        </div>
        """

    broken_links_html = ""
    if links:
        link_items = "".join(
            f"<li><strong>{escape(l.get('url', 'Project Link'))}</strong>: {escape(l.get('reason', 'Returned 404 or broken'))}</li>"
            for l in links
        )
        broken_links_html = f"""
        <div style="margin-top:20px;padding:14px;background-color:{WARNING_BG};border:1px solid {WARNING_BORDER};border-radius:8px;">
            <h4 style="margin:0 0 6px 0;font-size:14px;color:{WARNING_TEXT};font-family:{FONT};">⚠️ Action Required: Fix Broken Resume Links</h4>
            <p style="margin:0 0 8px 0;font-size:13px;color:{WARNING_TEXT};font-family:{FONT};">
                During automated review, we detected that the following links on your resume could not be accessed:
            </p>
            <ul style="margin:0;padding-left:20px;font-size:13px;color:{WARNING_TEXT};font-family:{FONT};">{link_items}</ul>
        </div>
        """

    mentor_html = ""
    if mentor_notes:
        mentor_html = f"""
        <div style="margin-top:20px;padding:14px;background:#F9FAFB;border-radius:8px;border:1px solid {LINE};">
            <h4 style="margin:0 0 6px 0;font-size:14px;color:{NAVY};font-family:{FONT};">Evaluation Feedback Notes</h4>
            <p style="margin:0;font-size:14px;line-height:22px;color:{INK};font-family:{FONT};white-space:pre-wrap;">{escape(mentor_notes)}</p>
        </div>
        """

    recs_html = ""
    if recs:
        rec_items = "".join(f"<li style='margin-bottom:8px;'>{escape(step)}</li>" for step in recs)
        recs_html = f"""
        <div style="margin-top:20px;">
            <h3 style="margin:0 0 8px 0;font-size:15px;color:{NAVY};font-family:{FONT};">Recommended Next Steps</h3>
            <ol style="margin:0;padding-left:20px;color:{INK};font-size:14px;line-height:22px;font-family:{FONT};">{rec_items}</ol>
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:24px;background-color:#F3F4F6;font-family:{FONT};">
<table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width:600px;background:#FFFFFF;border-radius:12px;overflow:hidden;border:1px solid {LINE};">
    <tr>
        <td style="padding:28px 32px 20px 32px;background-color:{NAVY};">
            <div style="color:{YELLOW};font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;">Screening Evaluation</div>
            <h1 style="color:#FFFFFF;margin:0;font-size:20px;font-weight:700;line-height:28px;">Constructive Feedback for {escape(safe_role)}</h1>
        </td>
    </tr>
    <tr>
        <td style="padding:28px 32px 32px 32px;">
            <p style="font-size:15px;line-height:24px;color:{INK};margin:0 0 16px 0;">
                Hi <strong>{escape(safe_name)}</strong>,
            </p>
            <p style="font-size:14px;line-height:22px;color:{MUTED};margin:0 0 16px 0;">
                Thank you for participating in the technical screening for <strong>{escape(safe_role)}</strong>.
                Our team evaluated your profile and resume in detail. While you are not moving forward to client interviews for this specific opening, we want to provide concrete, personalized feedback to help you succeed in upcoming placements.
            </p>

            {strengths_html}
            {skills_html}
            {broken_links_html}
            {mentor_html}
            {recs_html}

            <p style="font-size:14px;line-height:22px;color:{MUTED};margin:28px 0 0 0;border-top:1px solid {LINE};padding-top:20px;">
                Keep building, practicing, and refining your work. Consistency is what leads to great outcomes.<br><br>
                Warm regards,<br>
                <strong>The Placement & Academic Team</strong>
            </p>
        </td>
    </tr>
</table>
</body>
</html>"""

    return subject, html_content, text_content


def build_company_pitch_email(
    *,
    company_name: str,
    hr_name: str,
    role_name: str,
    candidate_summaries: list[dict],
    pitch_summary: str,
    custom_notes: str | None = None,
) -> tuple[str, str, str]:
    """Build outbound email pitching shortlisted candidates to corporate HR partners.

    Returns:
        (subject, html_content, text_content)
    """
    safe_company = (company_name or "Partner").strip()
    safe_hr = (hr_name or "Hiring Team").strip()
    safe_role = (role_name or "Open Position").strip()
    candidates = candidate_summaries or []
    count = len(candidates)

    subject = f"Verified Candidate Profiles for {safe_role} | {safe_company}"

    # Plain text version
    text_lines = [
        f"Dear {safe_hr},",
        "",
        f"We are pleased to introduce {count} verified, job-ready candidates for the {safe_role} position at {safe_company}.",
        "",
        pitch_summary.strip() if pitch_summary else "",
        "",
    ]
    if custom_notes:
        text_lines.append(f"Note from Placement Team:\n{custom_notes}\n")

    text_lines.append("Shortlisted Candidates:")
    for idx, c in enumerate(candidates, 1):
        name = c.get("name", f"Candidate {idx}")
        score = c.get("score")
        skills = ", ".join(c.get("matched_skills", [])) or "Key Technical Skills"
        resume_link = c.get("resume_url") or "Available on request"
        readiness = c.get("readiness_score")
        score_str = f" | Overall Score: {score}%" if score is not None else ""
        readiness_str = f" | Readiness: {readiness}/10" if readiness else ""
        text_lines.append(f"{idx}. {name}{score_str}{readiness_str}")
        text_lines.append(f"   Skills: {skills}")
        if resume_link:
            text_lines.append(f"   View Resume: {resume_link}")
        text_lines.append("")

    text_lines.append("Please let us know your availability for scheduling introductory interviews.")
    text_lines.append("Best regards,\nThe Placement Partnerships Team\nThe Barabari Collective")
    text_content = "\n".join(text_lines)

    # HTML Candidate Rows
    candidate_rows_html = ""
    for idx, c in enumerate(candidates, 1):
        name = escape(str(c.get("name") or f"Candidate {idx}"))
        score = c.get("score")
        skills = c.get("matched_skills") or []
        skills_badge = "".join(f"<span style='display:inline-block;background:{SOFT};color:{BLUE};padding:3px 8px;border-radius:12px;font-size:12px;margin:2px 3px 2px 0;'>{escape(s)}</span>" for s in skills[:6])
        resume_url = c.get("resume_url")
        readiness = c.get("readiness_score")

        resume_btn = ""
        if resume_url:
            resume_btn = f"""
            <a href="{escape(resume_url)}" target="_blank" style="display:inline-block;padding:6px 14px;background-color:{BLUE};color:#FFFFFF;text-decoration:none;font-size:13px;font-weight:600;border-radius:6px;margin-top:6px;">View Resume PDF</a>
            """

        score_badge = f"<span style='background:#E8F5E9;color:#2E7D32;padding:3px 8px;border-radius:12px;font-size:12px;font-weight:700;'>Score: {score}%</span>" if score is not None else ""
        readiness_badge = f"<span style='background:#FFF3E0;color:#E65100;padding:3px 8px;border-radius:12px;font-size:12px;font-weight:700;'>Readiness: {readiness}/10</span>" if readiness else ""

        candidate_rows_html += f"""
        <div style="border:1px solid {LINE};border-radius:8px;padding:16px;margin-bottom:12px;background:#FFFFFF;">
            <table width="100%" border="0" cellpadding="0" cellspacing="0">
                <tr>
                    <td>
                        <h4 style="margin:0 0 4px 0;font-size:16px;color:{NAVY};font-family:{FONT};">{name}</h4>
                        <div style="margin-bottom:8px;">{score_badge} {readiness_badge}</div>
                    </td>
                    <td align="right" valign="top">
                        {resume_btn}
                    </td>
                </tr>
            </table>
            <div style="margin-top:6px;">
                <div style="font-size:12px;color:{MUTED};margin-bottom:4px;font-family:{FONT};">Verified Skills:</div>
                <div>{skills_badge}</div>
            </div>
        </div>
        """

    notes_html = ""
    if custom_notes:
        notes_html = f"""
        <div style="margin:16px 0;padding:14px;background-color:{SOFT};border-radius:8px;border-left:4px solid {BLUE};">
            <h4 style="margin:0 0 4px 0;font-size:13px;text-transform:uppercase;color:{BLUE};letter-spacing:0.5px;">Placement Team Note</h4>
            <p style="margin:0;font-size:14px;line-height:22px;color:{INK};">{escape(custom_notes)}</p>
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:24px;background-color:#F3F4F6;font-family:{FONT};">
<table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width:650px;background:#FFFFFF;border-radius:12px;overflow:hidden;border:1px solid {LINE};">
    <tr>
        <td style="padding:28px 32px 20px 32px;background-color:{NAVY};">
            <div style="color:{YELLOW};font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;">Placement Shortlist</div>
            <h1 style="color:#FFFFFF;margin:0;font-size:22px;font-weight:700;line-height:30px;">Shortlisted Candidates for {escape(safe_role)}</h1>
        </td>
    </tr>
    <tr>
        <td style="padding:28px 32px 32px 32px;">
            <p style="font-size:15px;line-height:24px;color:{INK};margin:0 0 14px 0;">
                Dear <strong>{escape(safe_hr)}</strong>,
            </p>
            <p style="font-size:14px;line-height:22px;color:{MUTED};margin:0 0 16px 0;">
                {escape(pitch_summary)}
            </p>

            {notes_html}

            <h3 style="margin:24px 0 12px 0;font-size:16px;color:{NAVY};">Candidate Profiles ({count})</h3>
            {candidate_rows_html}

            <p style="font-size:14px;line-height:22px;color:{MUTED};margin:28px 0 0 0;border-top:1px solid {LINE};padding-top:20px;">
                Please reply to this email or contact our partnerships team to schedule interview rounds with any of the candidates above.<br><br>
                Warm regards,<br>
                <strong>The Placement Partnerships Team</strong><br>
                The Barabari Collective
            </p>
        </td>
    </tr>
</table>
</body>
</html>"""

    return subject, html_content, text_content


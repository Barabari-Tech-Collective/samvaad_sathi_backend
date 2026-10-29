"""Untrusted resume strings must never become PDF renderer markup or resources."""
from html import escape
from urllib.parse import urlsplit

_LINK_FIELDS = {"github_link", "hosted_link", "githubUrl", "liveUrl"}


def safe_resume_markup_data(value, field=""):
    if isinstance(value, dict):
        return {key: safe_resume_markup_data(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_resume_markup_data(item, field) for item in value]
    if not isinstance(value, str):
        return value
    if field in _LINK_FIELDS:
        try:
            parsed = urlsplit(value)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                return ""
            if parsed.username or parsed.password or any(ord(char) < 32 for char in value):
                return ""
        except ValueError:
            return ""
    return escape(value, quote=True)


def deny_resume_resource(uri, relative_uri=None):
    # The text-only template needs no network, local file, data URI or external font.
    # Do not return the original URI: the renderer would fetch it itself.
    raise ValueError("External resources are not permitted in resume documents")

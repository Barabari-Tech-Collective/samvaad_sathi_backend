"""Per-person restrictions layered over existing role and data-scope authorization."""
from fastapi import HTTPException

CATALOGUE = {
    "practice_support": {"label": "Course practice support queue", "operations": ["read"]},
    "placement_shortlists": {"label": "Course placement shortlists", "operations": ["read", "create", "update", "publish", "delete"]},
    "interview_authoring": {"label": "Interview templates and question banks", "operations": ["read", "create", "update", "delete", "publish", "generate"]},
    "course_workspace": {"label": "Course workspace", "operations": ["read", "update", "export"]},
    "analytics": {"label": "Reports and analytics", "operations": ["read"]},
    "practice_coaching": {"label": "Practice coaching", "operations": ["read", "create", "update", "delete"]},
    "bulk_ats": {"label": "Bulk ATS", "operations": ["read", "create", "update", "delete"]},
    "teams": {"label": "Members and teams", "operations": ["read", "create", "update", "delete"]},
}


def validate_permissions(value):
    if not isinstance(value, dict):
        raise ValueError("Permissions must be a feature-to-operations object.")
    normalized = {}
    for feature, operations in value.items():
        if feature not in CATALOGUE or not isinstance(operations, list):
            raise ValueError("Unknown feature or invalid operation list.")
        if any(not isinstance(op, str) or op not in CATALOGUE[feature]["operations"] for op in operations):
            raise ValueError("Unknown operation.")
        if operations and "read" not in operations:
            raise ValueError("Enable read before granting other operations.")
        normalized[feature] = sorted(set(operations))
    return normalized


def permits(policy, feature, operation):
    # NULL retains existing role authorization for gradual rollout; {} revokes all.
    if policy is None:
        return True
    try:
        valid = validate_permissions(policy)
    except ValueError:
        return False
    return operation in valid.get(feature, []) and "read" in valid.get(feature, [])


def require_permission(policy, feature, operation):
    if not permits(policy, feature, operation):
        raise HTTPException(403, "This feature or operation has been revoked for your account.")


def route_operation(method, path):
    if path.rstrip("/").endswith("/finalize"):
        return "publish"
    if path.rstrip("/").endswith("/archive"):
        return "delete"
    if path.rstrip("/").endswith("/export"):
        return "export"
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    if "/questions/generate" in path or "/regenerate" in path or "/extract-skills" in path:
        return "generate"
    if "/review" in path or "/submit" in path:
        return "publish"
    if method == "DELETE":
        return "delete"
    if method in {"PUT", "PATCH"} or "/cancel" in path:
        return "update"
    return "create"

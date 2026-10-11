"""Fail-closed API resource policy; route IDs are never sufficient authority."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth.ownership import require_admin, require_owner
from app.config import settings

_RESOURCE_PARAMS = {
    "task_id": "task",
    "material_id": "material",
    "report_id": "report",
    "variant_id": "template",
    "thread_id": "thread",
    "version_id": "report_version",
    "base_version_id": "report_version",
    "target_version_id": "report_version",
    "comparison_id": "comparison",
    "delta_id": "report_delta",
    "proposal_id": "proposal",
    "notification_id": "notification",
    "job_id": "style_job",
}
_NESTED_PARAMS = {"sentence_id": "report_id", "issue_id": "report_id", "item_id": "comparison_id"}


def required_resources(path_params: dict) -> list[tuple[str, str | int]]:
    result = []
    for name, value in path_params.items():
        if name in _RESOURCE_PARAMS:
            result.append((_RESOURCE_PARAMS[name], value))
        elif name in _NESTED_PARAMS:
            if _NESTED_PARAMS[name] not in path_params:
                raise ValueError("UNMAPPED_ROUTE_PARAMETER")
        elif name != "action":
            raise ValueError("UNMAPPED_ROUTE_PARAMETER")
    return result


def authorize_request(request: Request) -> None:
    model_settings = request.url.path.startswith("/api/system/model-connection")
    if (not settings.multiuser_enabled and not model_settings) or request.url.path == "/api/health":
        return
    from app.api.auth_routes import current_principal

    principal = current_principal(request)
    from app.auth.guards import validate_mutation_origin
    validate_mutation_origin(request)
    request.state.principal = principal
    if request.url.path.startswith("/api/system/"):
        require_admin(principal)
    try:
        requirements = required_resources(request.path_params)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    for kind, identifier in requirements:
        if kind == "style_job":
            from app.memory.style_jobs import get_job
            job = get_job(str(identifier))
            if job is None or job.get("owner_user_id") != principal.user_id:
                raise HTTPException(status_code=404, detail="NOT_FOUND")
        else:
            require_owner(kind, identifier, principal)

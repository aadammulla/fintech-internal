"""Map domain exceptions to HTML error pages (and audit denied access attempts)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.audit import record_event
from app.core.db import connect, transaction
from app.core.errors import DomainError, PermissionDenied
from app.core.identity import load_user_from_request
from app.core.templating import render

TITLES = {400: "Invalid request", 401: "Choose an identity", 403: "Not allowed",
          404: "Not found", 409: "Conflict"}


def _error_page(request: Request, status: int, message: str):
    return render(request, "error.html",
                  {"status": status, "title": TITLES.get(status, "Error"), "message": message},
                  status_code=status)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PermissionDenied)
    async def permission_denied(request: Request, exc: PermissionDenied):
        conn = connect(request.app.state.db_path)
        try:
            user = load_user_from_request(request, conn)
            with transaction(conn):
                record_event(conn, actor=user, action="access.denied",
                             target_type=exc.target_type or "route",
                             target_id=exc.target_id if exc.target_id is not None else request.url.path,
                             details={"permission": exc.permission, "reason": exc.message,
                                      "method": request.method, "path": request.url.path})
        finally:
            conn.close()
        return _error_page(request, 403, exc.message)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return _error_page(request, exc.status_code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return _error_page(request, exc.status_code, str(exc.detail))

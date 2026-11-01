"""Domain exceptions. Raise these from service code; app/core/handlers.py maps them to HTTP responses."""


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotAuthenticated(DomainError):
    status_code = 401


class PermissionDenied(DomainError):
    status_code = 403

    def __init__(self, message: str, *, permission: str | None = None,
                 target_type: str | None = None, target_id: str | int | None = None):
        super().__init__(message)
        self.permission = permission
        self.target_type = target_type
        self.target_id = target_id


class NotFound(DomainError):
    status_code = 404


class InvalidTransition(DomainError):
    status_code = 409


class ValidationError(DomainError):
    status_code = 400

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors

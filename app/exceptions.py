from typing import Any


class DomainError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        fields: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields


def not_found(resource: str) -> DomainError:
    return DomainError(404, "not_found", f"{resource} was not found")

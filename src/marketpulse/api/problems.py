"""Errors as RFC 9457 problem details (application/problem+json)."""

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    def __init__(self, status: int, detail: str, type_: str = "about:blank") -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.type = type_


def problem(request: Request, status: int, detail: str, type_: str = "about:blank") -> JSONResponse:
    return JSONResponse(
        {
            "type": type_,
            "title": HTTPStatus(status).phrase,
            "status": status,
            "detail": detail,
            "instance": request.url.path,
        },
        status_code=status,
        media_type=PROBLEM_JSON,
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def on_problem(request: Request, error: ProblemError) -> JSONResponse:
        return problem(request, error.status, error.detail, error.type)

    @app.exception_handler(HTTPException)
    async def on_http_error(request: Request, error: HTTPException) -> JSONResponse:
        return problem(request, error.status_code, str(error.detail))

    @app.exception_handler(RequestValidationError)
    async def on_invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
            for issue in error.errors()
        )
        return problem(request, 422, details)

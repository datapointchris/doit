"""The failures the service reports to its caller as a sentence, and the one shape every error answers.

Every error answers `{"error": <status text>, "message": <reason>}`: one the
service raises, a refused bearer from pyapiauth, a request FastAPI could not
validate, and the router's own 404 and 405. A client decoding one error decodes
them all.

A layer below the routes raises a `RequestFailed`, so it decides what the caller
is told and the routes never catch. A bug is not one of these: it reaches the log
as a traceback and the caller as a bare 500.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

log = logging.getLogger(__name__)


class RequestFailed(RuntimeError):
    """The request could not be answered, for a reason worth one sentence."""

    status = 500


class BadRequest(RequestFailed):
    """The caller asked for something malformed, or sent something the store refuses."""

    status = 422


class NotFound(RequestFailed):
    """The caller named something that is not there."""

    status = 404


class Conflict(RequestFailed):
    """The request contradicts what is held."""

    status = 409


def body(status: int, message: str) -> dict[str, str]:
    try:
        phrase = HTTPStatus(status).phrase
    except ValueError:
        phrase = 'Error'
    return {'error': phrase, 'message': message}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestFailed)
    async def failed(request: Request, error: RequestFailed) -> JSONResponse:
        level = logging.ERROR if error.status >= 500 else logging.INFO
        log.log(level, 'request_failed', extra={'path': request.url.path, 'status': error.status, 'error': str(error)})
        return JSONResponse(body(error.status, str(error)), status_code=error.status)

    @app.exception_handler(HTTPException)
    async def refused(request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(body(error.status_code, str(error.detail)), status_code=error.status_code, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def malformed(request: Request, error: RequestValidationError) -> JSONResponse:
        problems = '; '.join(f'{".".join(str(part) for part in problem["loc"])}: {problem["msg"]}' for problem in error.errors())
        return JSONResponse(body(422, problems or 'the request is malformed'), status_code=422)

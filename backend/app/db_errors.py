"""Bounded backpressure for identifiable database capacity exhaustion."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, TimeoutError


def register_capacity_handlers(app: FastAPI) -> None:
    async def capacity_error(_request: Request, error: Exception):
        if isinstance(error, OperationalError):
            original = error.orig
            if getattr(original, "sqlstate", None) != "53300" and "EMAXCONNSESSION" not in str(original):
                # Not every OperationalError is overload (bad schema, network,
                # query cancellation). Keep its real failure classification.
                raise error
        return JSONResponse(
            status_code=503,
            content={"detail": "서버 저장소가 혼잡해요. 잠시 후 다시 연결합니다.", "code": "database_busy"},
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )

    app.add_exception_handler(TimeoutError, capacity_error)
    app.add_exception_handler(OperationalError, capacity_error)

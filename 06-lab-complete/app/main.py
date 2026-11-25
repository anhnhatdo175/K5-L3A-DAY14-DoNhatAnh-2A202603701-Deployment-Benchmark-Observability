"""
Production AI Agent — Part 6 Final Project

Đạt 12 tiêu chí trong README.md, kiểm tra bằng:
    python check_production_ready.py
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log của app là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"extra_fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan — startup / graceful shutdown
# ─────────────────────────────────────────────────────────
STATE = {"ready": False, "in_flight": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Không ping Redis ở đây: agent phải khởi động được kể cả khi Redis chưa lên
    # (/health vẫn 200, /ready trả 503 cho tới khi Redis kết nối được).
    STATE["ready"] = True
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute)
    yield
    # Uvicorn nhận SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy xong
    # → chạy phần shutdown của lifespan này → process thoát với exit code 0.
    STATE["ready"] = False
    log_event("shutdown", in_flight=STATE["in_flight"])


app = FastAPI(title="Production AI Agent", lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs")


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.perf_counter()
    STATE["in_flight"] += 1
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        STATE["in_flight"] -= 1
        log_event("request", method=request.method, path=request.url.path, status=status,
                  ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Error handling — mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "msg": e.get("msg")}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "invalid_request", "details": errors})


@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    log_event("redis_unavailable", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"error": "service_unavailable",
                                                  "detail": "Backing store unavailable, retry later"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/health")
def health():
    """Liveness: process còn sống → 200 (không phụ thuộc Redis)."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được và app chưa shutdown."""
    if STATE["ready"] and redis_healthy():
        return {"status": "ready"}
    return JSONResponse(status_code=503, content={"status": "not ready"})


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)          # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                     # 402 khi hết budget

    answer = llm_ask(body.question)

    cost = record_cost(estimate_tokens(body.question), estimate_tokens(answer))
    log_event("llm_call", user_id=body.user_id, q_chars=len(body.question),
              a_chars=len(answer), cost_usd=round(cost, 8))
    return {"question": body.question, "answer": answer}

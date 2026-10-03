"""Small HTTP adapter around the existing manual agent."""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException

from app.agent import ModelIntegrationError, ToolInvocationError, run_agent
from app.config import ConfigurationError, Settings, get_settings
from app.database import DatabaseUnavailableError
from app.profiles import DATABASE_PROFILES, PUBLIC_DATABASES, resolve_profile
from app.trace import AgentTrace, safe_trace_value


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    database: str

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question must contain text.")
        return value

    @field_validator("database")
    @classmethod
    def allowed_profile(cls, value: str) -> str:
        if value not in DATABASE_PROFILES:
            raise ValueError("Unknown database profile.")
        return value


class QuerySuccess(BaseModel):
    status: Literal["success"] = "success"
    answer: str
    run_id: str
    trace_path: str | None
    database: str


class QueryError(BaseModel):
    status: Literal["error"] = "error"
    error_type: str
    message: str
    run_id: str | None = None
    trace_path: str | None = None


class PublicDatabase(BaseModel):
    id: str
    name: str
    description: str


def error_response(code: int, error_type: str, message: str,
                   trace: AgentTrace | None = None) -> JSONResponse:
    body = QueryError(
        error_type=error_type, message=message,
        run_id=trace.run_id if trace else None,
        trace_path=trace.trace_path if trace else None,
    )
    return JSONResponse(status_code=code, content=safe_trace_value(body.model_dump()))


async def invalid_request(request: Request, error: RequestValidationError):
    # FastAPI's default validation response includes the submitted input.
    # Return fixed feedback so even an invalid body cannot echo credentials.
    return error_response(
        400, "invalid_request",
        "Provide a question of 1–4000 characters and database profile sales or saas; "
        "no additional fields are allowed.",
    )


def health() -> dict:
    return {"status": "ok"}


def databases() -> list[PublicDatabase]:
    return [PublicDatabase(**profile) for profile in PUBLIC_DATABASES]


def ready():
    """Configuration readiness only; does not call the model or PostgreSQL."""
    try:
        settings = get_settings(load_environment=False)
        if not settings.google_api_key:
            raise ConfigurationError("Required model configuration is missing.")
        for profile in DATABASE_PROFILES:
            resolve_profile(profile, settings)
    except Exception:
        return error_response(503, "service_unavailable", "Service configuration is unavailable.")
    return {"status": "ready"}


def query(body: QueryRequest):
    # A synchronous endpoint runs the blocking agent in FastAPI's worker pool.
    trace = None
    try:
        target = resolve_profile(body.database)
        trace = AgentTrace(source="api", database_profile=body.database)
        answer = run_agent(
            body.question, trace=trace, verbose=False,
            database_name=target.database_name, database_url=target.url,
        )
        return QuerySuccess(
            answer=safe_trace_value(answer), run_id=trace.run_id,
            trace_path=safe_trace_value(trace.trace_path), database=body.database,
        )
    except ConfigurationError:
        return error_response(503, "service_unavailable", "Service configuration is unavailable.", trace)
    except DatabaseUnavailableError:
        return error_response(503, "database_unavailable", "The database is unavailable.", trace)
    except ModelIntegrationError:
        return error_response(503, "model_integration_error",
                              "The model could not complete this request.", trace)
    except ToolInvocationError:
        return error_response(500, "tool_runtime_error",
                              "A tool could not complete this request.", trace)
    except Exception:
        if trace is not None and trace.termination_reason == "iteration_limit":
            return error_response(503, "iteration_limit",
                                  "The agent reached its response limit without a final answer.", trace)
        return error_response(500, "internal_error",
                              "The request could not be completed.", trace)


async def http_error(request: Request, error: HTTPException):
    # Do not echo exception details, requested URLs, or headers.
    return error_response(error.status_code, "http_error", "The HTTP request could not be served.")


async def server_error(request: Request, error: Exception):
    return error_response(500, "internal_error", "The request could not be completed.")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Configure CORS once at startup; request profile resolution never edits env."""
    settings = settings if settings is not None else get_settings()
    service = FastAPI(title="Agentic Data Analyst", debug=False)
    service.add_middleware(
        CORSMiddleware, allow_origins=list(settings.allowed_origins),
        allow_credentials=False, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    service.add_exception_handler(RequestValidationError, invalid_request)
    service.add_exception_handler(HTTPException, http_error)
    service.add_exception_handler(Exception, server_error)
    service.add_api_route("/health", health, methods=["GET"])
    service.add_api_route("/ready", ready, methods=["GET"], responses={503: {"model": QueryError}})
    service.add_api_route("/databases", databases, methods=["GET"], response_model=list[PublicDatabase])
    service.add_api_route(
        "/query", query, methods=["POST"], response_model=QuerySuccess,
        responses={400: {"model": QueryError}, 500: {"model": QueryError}, 503: {"model": QueryError}},
    )
    return service


app = create_app()

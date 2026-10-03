"""Minimal manual tool-calling loop for the data analyst agent."""

import sys
import time

from langchain.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from app.database import DatabaseUnavailableError, database_context
from app.llm import get_llm
from app.trace import AgentTrace, ToolCallRecord, elapsed_ms, safe_trace_value, utc_now
from app.tools import calculator, describe_table, execute_sql, get_schema

TOOLS = [
    calculator,
    get_schema,
    describe_table,
    execute_sql,
]
TOOL_MAP = {tool.name: tool for tool in TOOLS}

SYSTEM_INSTRUCTIONS = """You are a data analyst agent.
Never invent database tables, columns, or query results.
For an unfamiliar database question, inspect the schema with get_schema.
Use describe_table only for necessary details such as relationships or business units;
do not automatically describe every table. Follow trusted column descriptions.
Exact stored categorical/text filter values are database semantics; do not assume capitalization, spelling, or codes.
When a literal's stored representation is unknown, prefer trusted describe_table metadata that defines allowed values.
Otherwise, when necessary, use a small bounded read-only SELECT DISTINCT query with ORDER BY and LIMIT 10.
Do not treat a bounded sample as an exhaustive list of allowed values.
A zero-row result or zero count from an inferred, unverified categorical literal is insufficient evidence:
verify stored values before concluding no matches; correct the literal and rerun the analytical query if needed.
Reuse literals established by trusted metadata or previous tool results; do not inspect before every equality filter.
An exact database code supplied in the question still needs confirmation from metadata or observed values.
Once the stored representation is established, a zero result is valid; conclude without repeated verification.
Preserve exact equality semantics; do not globally substitute case-insensitive matching.
Use execute_sql to obtain real data, and never claim a database result without it.
Prefer read-only analytical SQL.
An execute_sql error is an observation, not a final answer. Read the error carefully.
If a table, column, or relationship may be wrong, use get_schema or describe_table as needed.
Revise the SQL before retrying; do not repeat the exact failed query unchanged.
Tool argument validation errors are recoverable observations. Read the feedback
and call the tool again with its declared arguments; never repeat an invalid call unchanged.
Do not fabricate a result when a tool invocation failed.
Only give a factual database answer after a successful execute_sql result.
If reasonable correction attempts fail, say the query could not be completed; do not invent a result.
Do not expose database credentials or internal configuration.
Give the user a concise natural-language answer based on real tool results.
Once successful results provide sufficient evidence, stop exploring and answer.
A verified zero-row result from the correct filters is valid: report no matching records.
Do not repeat status/schema/result checks or reopen resolved assumptions without contradictory evidence.
Do not cross-check sales after a sufficient normalized query unless investigating a real inconsistency.
Preserve response budget for the final answer within the maximum model responses.
Choose only necessary tools, while preserving correctness and sufficient evidence."""

DEFAULT_QUESTION = "How many rows are in the database sales data?"


class ToolInvocationError(RuntimeError):
    """A tool failed before it could return an observation."""


class ModelIntegrationError(RuntimeError):
    """A model operation failed; retain only allowlisted diagnostic fields."""

    def __init__(self, exc: Exception, phase: str, model_turn: int):
        categories = {
            "INVALID_ARGUMENT", "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND",
            "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "UNAVAILABLE", "INTERNAL", "CANCELLED",
        }
        http_categories = {
            400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED",
            404: "NOT_FOUND", 408: "DEADLINE_EXCEEDED", 429: "RESOURCE_EXHAUSTED",
            500: "INTERNAL", 503: "UNAVAILABLE", 504: "DEADLINE_EXCEEDED",
        }
        category = "unknown"
        # The Gemini SDK wraps provider exceptions using __cause__. Never retain
        # their messages, request/response objects, headers, or connection details.
        for error in (exc, exc.__cause__):
            if error is None:
                continue
            for name in ("status", "code", "status_code"):
                value = getattr(error, name, None)
                if isinstance(value, str) and value in categories:
                    category = value
                elif type(value) is int and value in http_categories:
                    category = http_categories[value]
        self.diagnostics = {
            "phase": phase,
            "exception_type": safe_trace_value(type(exc).__name__),
            "provider_category": category,
            "model_turn": model_turn,
        }
        super().__init__(
            f"Model integration failed ({self.diagnostics['exception_type']}; "
            f"phase={phase}; turn={model_turn}; category={category})."
        )


def validation_feedback(tool, schema, arguments: dict, error: ValidationError) -> str:
    """Describe the declared fields and error codes without raw input values."""
    definition = schema.model_json_schema()
    required = definition.get("required", [])
    fields = [
        f"{name}: {details.get('type', 'see tool schema')}"
        + (" (required)" if name in required else "")
        for name, details in definition.get("properties", {}).items()
    ]
    errors = [
        ".".join(str(part) for part in item["loc"]) + ": " + item["type"]
        for item in error.errors(include_input=False, include_context=False, include_url=False)[:3]
    ]
    return safe_trace_value(
        "Tool argument validation error: " + "; ".join(errors)
        + ". Expected fields: " + ", ".join(fields)
        + ". Received fields: " + ", ".join(list(arguments)[:10])
        + f". Call {tool.name} again using the declared tool schema."
    )


def run_agent(
    question: str,
    max_iterations: int = 8,
    *,
    trace: AgentTrace | None = None,
    verbose: bool = True,
    database_name: str | None = None,
    database_url: str | None = None,
) -> str:
    """Observe one manual run and persist its trace even when the run fails."""
    trace = trace if trace is not None else AgentTrace()
    trace.begin(question)
    try:
        with database_context(database_name, database_url=database_url):
            answer = _run_agent(question, max_iterations, trace=trace, verbose=verbose)
        trace.finish("success", "success")
        return answer
    except DatabaseUnavailableError:
        trace.finish("database_error", "database_unavailable")
        raise
    except ModelIntegrationError:
        trace.finish("model_error", "model_integration_error")
        raise
    except ToolInvocationError:
        trace.finish("tool_error", "tool_runtime_error")
        raise
    except BaseException as exc:
        # Finalize interrupted runs too, then preserve the original exception.
        limited = isinstance(exc, RuntimeError) and str(exc).startswith("Agent stopped after ")
        reason = "iteration_limit" if limited else "unexpected_error"
        trace.finish(reason, reason)
        raise
    finally:
        path = trace.persist()
        if verbose and path:
            print("Trace:", path)


def _run_agent(
    question: str,
    max_iterations: int = 8,
    *,
    trace: AgentTrace | None = None,
    verbose: bool = True,
) -> str:
    """Answer one question with the manual Gemini tool-calling loop."""
    phase = "model_setup"
    try:
        llm = get_llm()
        model_name = getattr(llm, "model", None)
        if isinstance(model_name, str):
            trace.model["name"] = safe_trace_value(model_name)
        phase = "tool_binding"
        llm_with_tools = llm.bind_tools(TOOLS)
    except Exception as exc:
        failure = ModelIntegrationError(exc, phase, 0)
        if trace is not None:
            trace.model_error = failure.diagnostics
        raise failure from None
    messages = [
        SystemMessage(content=SYSTEM_INSTRUCTIONS +
                      f"\nYou have at most {max_iterations} model responses, including the final answer."),
        HumanMessage(content=question),
    ]

    for turn in range(1, max_iterations + 1):
        turn_event, turn_started = trace.start_turn(turn)
        try:
            response = llm_with_tools.invoke(messages)
        except Exception as exc:
            failure = ModelIntegrationError(exc, "model_invocation", turn)
            if trace is not None:
                trace.model_error = failure.diagnostics
            trace.finish_turn(turn_event, turn_started, error=failure.diagnostics)
            raise failure from None
        trace.finish_turn(turn_event, turn_started, response=response)
        if trace is not None:
            trace.model_turns += 1
        messages.append(response)

        if not response.tool_calls:
            final_answer = response.text
            if trace is not None:
                trace.final_answer = safe_trace_value(final_answer)
            if verbose:
                print("Final answer:", final_answer)
            return final_answer

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_arguments = tool_call["args"]
            tool_started = time.perf_counter()
            event = ToolCallRecord(
                safe_trace_value(tool_name), safe_trace_value(tool_arguments), "",
                tool_call["id"], status="pending", model_turn=turn, started_at=utc_now(),
            )
            if trace is not None:
                trace.tool_calls.append(event)
            if verbose:
                print("Tool requested:", tool_name)
                print("Tool arguments:", event.arguments)

            selected_tool = TOOL_MAP.get(tool_name)
            if selected_tool is None:
                available_tools = ", ".join(TOOL_MAP)
                result = (
                    f"Unknown tool requested: {tool_name}. "
                    f"Available tools: {available_tools}."
                )
            else:
                try:
                    schema = selected_tool.get_input_schema()
                    # Validate separately so errors inside the tool body remain fatal.
                    # Invoke the original arguments unchanged after validation succeeds.
                    try:
                        schema.model_validate(tool_arguments)
                    except ValidationError as exc:
                        result = validation_feedback(selected_tool, schema, tool_arguments, exc)
                        event.status = "error"
                        event.error_type = "ValidationError"
                        event.error_category = "tool_argument_validation_error"
                    else:
                        result = selected_tool.invoke(tool_arguments)
                except Exception as exc:
                    event.status = "error"
                    event.error_type = type(exc).__name__
                    event.error_category = "tool_runtime_error"
                    # Exception text may include raw inputs or credentials.
                    event.error_message = "Tool invocation failed; check the recorded argument fields."
                    event.finished_at = utc_now()
                    event.duration_ms = elapsed_ms(tool_started)
                    if isinstance(exc, DatabaseUnavailableError):
                        event.error_category = "database_unavailable"
                        event.error_message = "The database is unavailable."
                        raise DatabaseUnavailableError(event.error_message) from None
                    raise ToolInvocationError(
                        f"Tool invocation failed ({event.error_type})."
                    ) from None

            event.result = safe_trace_value(str(result))
            if event.status == "pending":
                event.status = "success"
            if selected_tool is None:
                event.status = "error"
                event.error_type = "UnknownTool"
            elif tool_name == "execute_sql" and str(result).startswith(
                ("SQL execution error:", "Query rejected:")
            ):
                event.status = "error"
                event.error_type = "SQLExecutionError" if str(result).startswith("SQL execution error:") else "QueryRejected"
            if event.status == "error":
                event.error_message = event.result
            event.finished_at = utc_now()
            event.duration_ms = elapsed_ms(tool_started)
            if verbose:
                print("Tool result:", event.result)
            messages.append(
                ToolMessage(content=str(result), tool_call_id=tool_call["id"])
            )

    raise RuntimeError(
        f"Agent stopped after {max_iterations} model responses without a final answer."
    )


def main() -> None:
    question = " ".join(sys.argv[1:]).strip() or DEFAULT_QUESTION
    run_agent(question, trace=AgentTrace(source="cli"))


if __name__ == "__main__":
    main()

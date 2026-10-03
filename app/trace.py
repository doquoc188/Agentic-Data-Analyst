"""Local structured run traces. No model calls, SQL calls, or tracing services."""

import argparse
import json
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import ConfigurationError, get_settings, secret_values

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
PREVIEW_ROWS = 10
PREVIEW_CHARS = 4000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_trace_value(value):
    """Redact configured secrets, credential fields, headers, and connection URLs."""
    if isinstance(value, dict):
        counts = {"input_tokens", "output_tokens", "total_tokens"}
        def token_metadata(key, item):
            return (key in counts and type(item) is int and item >= 0) or (
                key == "token_usage" and isinstance(item, dict)
                and all(name in counts and type(count) is int and count >= 0
                        for name, count in item.items())
            )
        return {
            safe_trace_value(str(key)): "[REDACTED]" if not token_metadata(key, item) and re.search(
                r"password|api_key|secret|token|dsn|connection_string|authorization|headers|"
                r"^env$|environment|^request$", str(key), re.I
            ) else safe_trace_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_trace_value(item) for item in value]
    if isinstance(value, str):
        for secret in secret_values():
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"postgres(?:ql)?://\S+|[a-z][a-z0-9+.-]*://[^\s/]*:[^\s/]*@\S+",
                       "[REDACTED CONNECTION]", value, flags=re.I)
        value = re.sub(r"\bAuthorization\s*[:=]\s*[^\r\n]+|\bBearer\s+\S+",
                       "[REDACTED AUTHORIZATION]", value, flags=re.I)
        return re.sub(
            r"\b(password|api_key|token|secret)\s*[=:]\s*\S+",
            r"\1=[REDACTED]", value, flags=re.I,
        )
    return value


def result_preview(result: str) -> dict:
    """Preview the existing text renderer without interpreting cells as SQL data."""
    text = safe_trace_value(result)
    count = re.search(r"\n\nRows returned: (\d+)(?:\nResult truncated to \d+ rows\.)?\s*\Z", text)
    table = text.startswith("COLUMNS:\n") and "\nROWS:\n" in text
    row_count = int(count.group(1)) if table and count else None
    preview = text
    rows_shown = None
    if table:
        header, body = text.split("\nROWS:\n", 1)
        rows = body.split("\n\nRows returned:", 1)[0].strip().splitlines()
        if rows == ["(no rows)"]:
            rows = []
        rows_shown = min(len(rows), PREVIEW_ROWS)
        preview = header + "\nROWS:\n" + "\n".join(rows[:PREVIEW_ROWS])
    return {
        "type": "sql_table" if table else "text",
        "size_chars": len(text),
        "row_count": row_count,
        "rows_shown": rows_shown,
        "result_truncated": bool(count and "\nResult truncated to " in count.group(0)) if table and count else None,
        "preview_truncated": len(preview) > PREVIEW_CHARS or (table and len(rows) > PREVIEW_ROWS),
        "text": preview[:PREVIEW_CHARS],
    }


@dataclass
class ToolCallRecord:
    # The existing in-memory fields remain compatible with the evaluator.
    name: str
    arguments: dict
    result: str
    tool_call_id: str
    status: str = "success"
    error_type: str | None = None
    error_message: str | None = None
    error_category: str | None = None
    model_turn: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float = 0.0


@dataclass
class AgentTrace:
    """One run's observations; evaluator ground truth is never part of this model."""
    final_answer: str | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    model_turns: int = 0
    model_error: dict | None = None
    source: str = "other"
    case_id: str | None = None
    database_profile: str | None = None
    question: str = ""
    run_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    duration_ms: float = 0.0
    status: str = "pending"
    termination_reason: str | None = None
    model: dict = field(default_factory=lambda: {"provider": "google_genai", "name": None})
    turns: list[dict] = field(default_factory=list)
    trace_path: str | None = None
    _started: float = field(default_factory=time.perf_counter, repr=False)

    def begin(self, question: str):
        self.run_id = str(uuid4())
        self.started_at, self._started = utc_now(), time.perf_counter()
        self.question = safe_trace_value(question)
        self.finished_at = self.trace_path = self.final_answer = self.model_error = None
        self.status, self.termination_reason = "pending", None
        self.model_turns, self.duration_ms = 0, 0.0
        self.turns.clear()
        self.tool_calls.clear()
        self.model = {"provider": "google_genai", "name": None}

    def start_turn(self, number: int) -> tuple[dict, float]:
        turn = {"turn": number, "started_at": utc_now(), "finished_at": None,
                "duration_ms": 0.0, "status": "pending", "response_type": None,
                "tool_call_count": 0, "requested_tools": [], "error": None}
        self.turns.append(turn)
        return turn, time.perf_counter()

    def finish_turn(self, turn: dict, started: float, *, response=None, error=None):
        turn.update(finished_at=utc_now(), duration_ms=elapsed_ms(started),
                    status="error" if error else "success")
        if error:
            turn["error"] = safe_trace_value(error)
        else:
            calls = response.tool_calls
            turn.update(response_type="tool_calls" if calls else "text",
                        tool_call_count=len(calls), requested_tools=[call["name"] for call in calls])
            # Usage is optional. Only allowlisted numeric counts are retained.
            usage = getattr(response, "usage_metadata", None)
            if isinstance(usage, dict):
                turn["token_usage"] = {key: usage[key] for key in (
                    "input_tokens", "output_tokens", "total_tokens"
                ) if type(usage.get(key)) is int and usage[key] >= 0}

    def finish(self, status: str, reason: str):
        self.finished_at = utc_now()
        self.duration_ms = elapsed_ms(self._started)
        self.status = status
        self.termination_reason = safe_trace_value(reason)
        # Close any partial event after an unexpected local error, without
        # inventing a model response or retaining its exception text.
        for turn in self.turns:
            if turn["status"] == "pending":
                turn.update(status="error", finished_at=self.finished_at,
                            error={"exception_type": "UnexpectedError", "provider_category": "unknown"})
        for call in self.tool_calls:
            if call.finished_at is None and call.started_at is not None:
                call.finished_at = self.finished_at
                if call.status == "pending":
                    call.status = "error"
                    call.error_category = "unexpected_error"

    def document(self) -> dict:
        tools, sql = [], []
        for call in self.tool_calls:
            # Older fake/legacy events can represent SQL errors only in result text.
            sql_error = call.result.startswith(("SQL execution error:", "Query rejected:"))
            validation = call.error_category == "tool_argument_validation_error" or (
                call.error_category is None and call.error_type == "ValidationError")
            failed = call.status != "success" or sql_error
            error_type = call.error_type
            error_category = call.error_category
            if sql_error:
                rejected = call.result.startswith("Query rejected:")
                error_type = "QueryRejected" if rejected else "SQLExecutionError"
                error_category = "sql_rejection" if rejected else "sql_execution_error"
            entry = {
                "name": call.name, "tool_call_id": call.tool_call_id, "model_turn": call.model_turn,
                "arguments": call.arguments, "started_at": call.started_at,
                "finished_at": call.finished_at, "duration_ms": call.duration_ms,
                "status": "validation_error" if validation else "runtime_error" if failed else "success",
                "error_type": error_type, "error_category": error_category,
                "error_message": safe_trace_value(call.error_message or (call.result if sql_error else ""))[:PREVIEW_CHARS] or None,
                "result": result_preview(call.result),
            }
            tools.append(entry)
            if call.name == "execute_sql":
                sql.append({key: entry[key] for key in (
                    "tool_call_id", "model_turn", "started_at", "finished_at", "duration_ms",
                    "error_type", "error_category", "error_message"
                )} | {"query": call.arguments.get("query"), "status": "error" if failed else "success",
                     "row_count": entry["result"]["row_count"],
                     "result_truncated": entry["result"]["result_truncated"],
                     "preview_truncated": entry["result"]["preview_truncated"]})
        metrics = {
            "model_turn_count": len(self.turns) or self.model_turns,
            "tool_call_count": len(tools),
            "successful_tool_calls": sum(call["status"] == "success" for call in tools),
            "failed_tool_calls": sum(call["status"] != "success" for call in tools),
            "sql_call_count": len(sql),
            "sql_execution_errors": sum(call.name == "execute_sql" and call.result.startswith(
                "SQL execution error:") for call in self.tool_calls),
            "tool_validation_errors": sum(call["status"] == "validation_error" for call in tools),
            "model_errors": sum(turn["status"] == "error" for turn in self.turns)
                            or int(self.status == "model_error"),
            "schema_tool_calls": sum(call.name == "get_schema" for call in self.tool_calls),
            "describe_table_calls": sum(call.name == "describe_table" for call in self.tool_calls),
            "total_duration_ms": self.duration_ms,
        }
        return safe_trace_value({
            "schema_version": 1, "run_id": self.run_id, "started_at": self.started_at,
            "finished_at": self.finished_at, "duration_ms": self.duration_ms,
            "source": self.source, "case_id": self.case_id, "question": self.question,
            "database_profile": self.database_profile,
            "status": self.status, "termination_reason": self.termination_reason,
            "model": self.model, "model_error": self.model_error, "turns": self.turns,
            "tool_calls": tools, "sql_calls": sql, "final_answer": self.final_answer,
            "metrics": metrics,
        })

    def persist(self) -> str | None:
        """Atomic JSON publication; tracing failure cannot replace an agent result."""
        temporary = None
        try:
            settings = get_settings(load_environment=False)
            if not settings.trace_enabled:
                self.trace_path = None
                return None
            directory = settings.trace_dir or RUNS_DIR
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromisoformat(self.started_at).strftime("%Y%m%dT%H%M%SZ")
            target = directory / f"{stamp}_{self.run_id}.json"
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".trace-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(self.document(), handle, indent=2, ensure_ascii=False, allow_nan=False)
                handle.write("\n")
            temporary.replace(target)
            self.trace_path = str(target.resolve())
        except Exception:
            print("Trace warning: could not save the run trace.", file=sys.stderr)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return self.trace_path


def elapsed_ms(started: float) -> float:
    return round(max(0.0, time.perf_counter() - started) * 1000, 3)


def read_trace(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported trace schema version.")
    return safe_trace_value(document)


def summary_text(document: dict) -> str:
    lines = [f"RUN {document['run_id']} [{document['status']}]", f"Question: {document['question']}"]
    tools = document["tool_calls"]
    if document.get("model_error") and not document["turns"]:
        error = document["model_error"]
        lines.append(f"Model error: {error['phase']}; {error['provider_category']}")
    for turn in document["turns"]:
        lines.append(f"Turn {turn['turn']}: {turn['status']} ({turn['duration_ms']} ms)")
        if turn["error"]:
            lines.append("  Model error: " + turn["error"].get("provider_category", "unknown"))
        elif turn["response_type"] == "text":
            lines.append("  Final answer")
        else:
            lines.append("  Model requested: " + ", ".join(turn["requested_tools"]))
        for call in tools:
            if call["model_turn"] != turn["turn"]:
                continue
            lines.append(f"  Tool {call['name']} [{call['tool_call_id']}]: {call['status']} ({call['duration_ms']} ms)")
            if call["name"] == "execute_sql":
                lines.append("    SQL: " + str(call["arguments"].get("query", "<invalid arguments>")))
                lines.append(f"    Rows: {call['result']['row_count']}; truncated: {call['result']['result_truncated']}")
            if call["error_message"]:
                lines.append("    Error: " + call["error_message"])
    if document["final_answer"] is not None:
        lines.append("Answer: " + document["final_answer"])
    lines.append(f"Total: {document['duration_ms']} ms; termination: {document['termination_reason']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a local agent run trace.")
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()
    if bool(args.path) == args.latest:
        parser.error("Provide a trace path or --latest.")
    try:
        path = args.path
        if args.latest:
            directory = get_settings().trace_dir or RUNS_DIR
            path = max(directory.glob("*.json"), key=lambda item: item.stat().st_mtime_ns)
        print(summary_text(read_trace(path)))
    except (OSError, ValueError, KeyError, ConfigurationError):
        parser.exit(1, "Trace reader: no readable supported trace was found.\n")


if __name__ == "__main__":
    main()

"""Ground-truth-free SQL selection from the final answer and saved observations."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def sql_execution_error(call) -> bool:
    return call.result.startswith("SQL execution error:")


def successful_sql(call) -> bool:
    return (
        call.name == "execute_sql"
        and call.status == "success"
        and bool(call.result)
        and not call.result.startswith(("SQL execution error:", "Query rejected:"))
    )


def observation_table(result: str, *, require_complete=False) -> dict:
    """Read the existing tool renderer for selection and legacy offline rescoring.

    Fresh evaluation still uses structured Psycopg results for scoring. The old
    pipe renderer is lossy; malformed or truncated saved tables are rejected.
    """
    if not result.startswith("COLUMNS:\n") or "\nROWS:\n" not in result:
        raise ValueError("No recognizable saved SQL table.")
    header, body = result[len("COLUMNS:\n"):].split("\nROWS:\n", 1)
    columns = [column.strip() for column in header.strip().split(" | ")]
    row_text = body.split("\n\nRows returned:", 1)[0].strip()
    count = re.search(r"\bRows returned: (\d+)", body)
    if "Result truncated" in body or (require_complete and count is None):
        raise ValueError("Saved SQL table is incomplete.")

    def cell(value):
        if value == "NULL":
            return None
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
            return Decimal(value)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", value):
            return datetime.fromisoformat(value)
        return value

    rows = [] if row_text == "(no rows)" else [
        [cell(value.strip()) for value in line.split(" | ")]
        for line in row_text.splitlines()
    ]
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("Ambiguous saved SQL table cells.")
    if count and int(count.group(1)) != len(rows):
        raise ValueError("Saved SQL row count is inconsistent.")
    return {"columns": columns, "rows": rows}


def select_answer_sql(tool_calls, final_answer: str | None, question: str = ""):
    """Choose by distinct answer evidence, question overlap, then recency.

    No benchmark results, reference SQL, or case contracts enter this heuristic.
    This estimates support; it does not prove SQL correctness or answer entailment.
    """
    answer = (final_answer or "").casefold()
    plain_answer = re.sub(r"[*_`#]", "", answer)
    # Numbered-list markers are presentation, not analytical measures.
    plain_answer = re.sub(r"(?m)^\s*\d+[.)]\s+", "", plain_answer)
    numbers = set()
    for value in re.findall(r"(?<!\w)[+-]?\d[\d,]*(?:\.\d+)?", plain_answer):
        try:
            numbers.add(Decimal(value.replace(",", "")))
        except InvalidOperation:
            pass

    def mentioned_key(value):
        if isinstance(value, Decimal):
            rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value in numbers:
                return ("number", value)
            if rounded in numbers:
                return ("number", rounded)
            return None
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        if value is None:
            return ("null", None) if re.search(r"\b(null|missing|unknown)\b", plain_answer) else None
        text = str(value).casefold()
        if text and re.search(r"(?<!\w)" + re.escape(text) + r"(?!\w)", plain_answer):
            return ("text", text)
        return None

    empty_answer = bool(re.search(r"\b(no|none|zero)\b", plain_answer))
    if re.search(r"\b(never|without|not)\b", question.casefold()):
        empty_answer |= bool(re.search(r"\b(every|all)\b", plain_answer))

    # Modest lexical tie-breaker favors a requested filter/entity over a later
    # inspection returning coincidentally equal numbers. No SQL parser is implied.
    question_words = {word.rstrip("s") for word in re.findall(r"[a-z]+", question.casefold()) if len(word) > 3}
    candidates = []
    for index, call in enumerate(tool_calls):
        if not successful_sql(call):
            continue
        numeric_matches, other_matches = set(), set()
        empty_support, best_row = 0, 0
        try:
            table = observation_table(call.result)
            empty_support = int(not table["rows"] and empty_answer)
            for row in table["rows"]:
                matched = {key for value in row if (key := mentioned_key(value)) is not None}
                best_row = max(best_row, len(matched))
                numeric_matches.update(key for key in matched if key[0] == "number")
                other_matches.update(key for key in matched if key[0] != "number")
        except ValueError:
            pass
        query_words = {word.rstrip("s") for word in re.findall(r"[a-z]+", call.arguments.get("query", "").casefold())}
        overlap = len(question_words & query_words)
        # Unmentioned cells do not dilute support. Repeated matching cells do
        # not add votes. A requested amount is stronger than a status word.
        score = (empty_support, len(numeric_matches), len(other_matches), best_row, overlap, index)
        candidates.append((score, call))
    if not candidates:
        return None, "No successful execute_sql query was produced."
    score, chosen = max(candidates, key=lambda item: item[0])
    if final_answer and not any(score[:3]):
        return None, "No successful execute_sql observation supports the recorded answer values."
    reason = (
        f"Distinct answer values: numeric={score[1]}, other={score[2]}, empty={score[0]}; "
        f"best-row matches={score[3]}; question-term overlap {score[4]}; "
        "latest call breaks remaining ties. Heuristic selection, not correctness scoring."
    )
    return chosen, reason


def tool_argument_validation_error(call) -> bool:
    """Recognize new argument errors and legacy validation-only trace events."""
    return call.status == "error" and (
        call.error_category == "tool_argument_validation_error"
        or (call.error_category is None and call.error_type == "ValidationError")
    )


def tool_invocation_error(call) -> bool:
    return call.status == "error" and (not call.result or tool_argument_validation_error(call))


def tool_validation_metrics(tool_calls, passed: bool) -> dict:
    """Recovery requires later success of the same tool and a passing case."""
    failed_tools = set()
    recovered = False
    for call in tool_calls:
        if tool_argument_validation_error(call):
            failed_tools.add(call.name)
        elif (
            call.name in failed_tools and call.status == "success"
            and call.error_type is None
            and (call.name != "execute_sql" or successful_sql(call))
            and not call.result.startswith(("SQL execution error:", "Query rejected:"))
        ):
            recovered = True
    return {
        "tool_invocation_errors": sum(tool_invocation_error(call) for call in tool_calls),
        "tool_argument_validation_errors": sum(tool_argument_validation_error(call) for call in tool_calls),
        "recovered_after_tool_validation_error": bool(passed and recovered),
    }


def sql_metrics(tool_calls, passed: bool) -> dict:
    """Count execution failures separately from exploration and invocation errors."""
    calls = [call for call in tool_calls if call.name == "execute_sql"]
    errors = [index for index, call in enumerate(calls) if sql_execution_error(call)]
    later_success = any(
        successful_sql(call) for index, call in enumerate(calls)
        if any(error < index for error in errors)
    )
    return {
        "sql_attempts": len(calls),
        "sql_execution_errors": len(errors),
        "sql_invocation_errors": sum(tool_invocation_error(call) for call in calls),
        "sql_rejections": sum(call.result.startswith("Query rejected:") for call in calls),
        "recovered_after_sql_error": bool(passed and later_success),
    }

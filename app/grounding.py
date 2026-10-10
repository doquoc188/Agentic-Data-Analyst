"""Conservative final-answer cleanup using evidence already seen by the Agent."""

import re
import unicodedata
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation


NUMBER_TEXT = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
NUMBER_PATTERN = re.compile(rf"(?<![\w.])(?P<number>{NUMBER_TEXT})(?!\w)")
PREFIX_MARKER_PATTERN = re.compile(
    rf"(?P<marker>\S)(?P<spacing>[ \t]*)(?P<number>{NUMBER_TEXT})(?!\w)"
)
SUFFIX_MARKER_PATTERN = re.compile(
    rf"(?<![\w.])(?P<number>{NUMBER_TEXT})(?P<spacing>[ \t]*)(?P<marker>\S)"
)
PREFIX_CODE_PATTERN = re.compile(
    rf"(?<![A-Za-z])(?P<marker>[A-Z]{{3}})[ \t]+(?P<number>{NUMBER_TEXT})(?!\w)"
)
SUFFIX_CODE_PATTERN = re.compile(
    rf"(?<![\w.])(?P<number>{NUMBER_TEXT})[ \t]+(?P<marker>[A-Z]{{3}})(?![A-Za-z])"
)
PARENTHETICAL_PATTERN = re.compile(r"(?P<spacing>[ \t]*)\((?P<label>[^()\r\n]{1,80})\)")
WORD_PATTERN = re.compile(r"[^\W\d_]+", re.UNICODE)


def guard_final_answer(question: str, tool_outputs: Iterable[str], answer: str) -> str:
    """Remove only unsupported currency markers and parenthetical display labels."""
    outputs = [str(output) for output in tool_outputs]
    evidence = "\n".join([question, *outputs])
    supported_numbers = _numbers_in(evidence)

    guarded = _remove_unsupported_currency(answer, evidence, supported_numbers)
    return _remove_unsupported_aliases(guarded, question, outputs)


def _numbers_in(text: str) -> set[Decimal]:
    numbers = set()
    for match in NUMBER_PATTERN.finditer(text):
        try:
            numbers.add(Decimal(match.group("number").replace(",", "")))
        except InvalidOperation:
            continue
    return numbers


def _number_is_supported(number: str, supported_numbers: set[Decimal]) -> bool:
    try:
        return Decimal(number.replace(",", "")) in supported_numbers
    except InvalidOperation:
        return False


def _marker_is_supported(marker: str, evidence: str) -> bool:
    if len(marker) == 1:
        return marker in evidence
    return re.search(
        rf"(?<![A-Za-z]){re.escape(marker)}(?![A-Za-z])",
        evidence,
        re.IGNORECASE,
    ) is not None


def _remove_unsupported_currency(
    answer: str,
    evidence: str,
    supported_numbers: set[Decimal],
) -> str:
    def remove_symbol(match: re.Match) -> str:
        marker = match.group("marker")
        number = match.group("number")
        if (
            unicodedata.category(marker) == "Sc"
            and _number_is_supported(number, supported_numbers)
            and not _marker_is_supported(marker, evidence)
        ):
            return number
        return match.group(0)

    def remove_code(match: re.Match) -> str:
        marker = match.group("marker")
        number = match.group("number")
        if (
            _number_is_supported(number, supported_numbers)
            and not _marker_is_supported(marker, evidence)
        ):
            return number
        return match.group(0)

    guarded = PREFIX_MARKER_PATTERN.sub(remove_symbol, answer)
    guarded = SUFFIX_MARKER_PATTERN.sub(remove_symbol, guarded)
    guarded = PREFIX_CODE_PATTERN.sub(remove_code, guarded)
    return SUFFIX_CODE_PATTERN.sub(remove_code, guarded)


def _evidenced_text_values(tool_outputs: Iterable[str]) -> set[str]:
    values = set()
    for output in tool_outputs:
        in_rows = False
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if line == "ROWS:":
                in_rows = True
                continue
            if line.startswith("Rows returned:"):
                in_rows = False
                continue
            if not line or line == "(no rows)":
                continue
            if in_rows or " | " in line:
                cells = re.split(r"\s+\|\s+", line)
            elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:/-]*", line):
                cells = [line]
            else:
                continue
            for cell in cells:
                value = cell.strip()
                if value and any(character.isalpha() for character in value):
                    values.add(value)
    return values


def _looks_like_display_label(label: str) -> bool:
    words = WORD_PATTERN.findall(label)
    return bool(words) and all(word[0].isupper() for word in words)


def _preceded_by_value(text: str, value: str) -> bool:
    preceding = text.rstrip()
    for closing_marker in ("**", "__", "`"):
        if preceding.endswith(closing_marker):
            preceding = preceding[:-len(closing_marker)].rstrip()
            break
    if not preceding.casefold().endswith(value.casefold()):
        return False
    start = len(preceding) - len(value)
    return start == 0 or not (preceding[start - 1].isalnum() or preceding[start - 1] == "_")


def _remove_unsupported_aliases(
    answer: str,
    question: str,
    tool_outputs: list[str],
) -> str:
    evidence = "\n".join([question, *tool_outputs])
    evidence_folded = evidence.casefold()
    values = sorted(_evidenced_text_values(tool_outputs), key=len, reverse=True)

    pieces = []
    cursor = 0
    for match in PARENTHETICAL_PATTERN.finditer(answer):
        label = match.group("label").strip()
        before = answer[:match.start("spacing")]
        supported_value = next(
            (value for value in values if _preceded_by_value(before, value)),
            None,
        )
        should_remove = (
            supported_value is not None
            and _looks_like_display_label(label)
            and label.casefold() not in evidence_folded
        )
        if should_remove:
            pieces.append(answer[cursor:match.start("spacing")])
            cursor = match.end()
    pieces.append(answer[cursor:])
    return "".join(pieces)

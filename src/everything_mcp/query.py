"""Compile the supported Everything-style grammar into parameterized SQLite SQL.

Everything evaluates OR before implicit AND. Values never become SQL identifiers.
Packed filenames supply candidate rowids; exact case/wildcard checks remain in SQLite.
"""

from __future__ import annotations

import os
import re
from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import datetime, timedelta


class QueryError(ValueError):
    """Invalid or unsupported query; safe to display to an MCP client."""


@dataclass(frozen=True)
class QueryOptions:
    match_case: bool = False
    match_path: bool = False
    match_whole_word: bool = False
    regex: bool = False
    use_candidates: bool = True


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: tuple
    uses_candidates: bool = False


@dataclass(frozen=True)
class _Token:
    value: str
    quoted: bool = False
    literal_start: int | None = None


def tokenize(query: str) -> list[_Token]:
    if len(query) > 8192:
        raise QueryError("Query exceeds the 8192-character limit")
    result: list[_Token] = []
    buf: list[str] = []
    quoted = False
    has_quote = False
    literal_start = None
    for char in query:
        if char == '"':
            if not quoted and literal_start is None:
                literal_start = len(buf)
            quoted = not quoted
            has_quote = True
        elif not quoted and (char.isspace() or char == "|"):
            if buf or has_quote:
                result.append(_Token("".join(buf), has_quote, literal_start))
                buf, has_quote, literal_start = [], False, None
            if char == "|":
                result.append(_Token("|"))
        elif not quoted and char == "<" and (not buf or all(c == "!" for c in buf)):
            if buf:
                result.append(_Token("".join(buf)))
                buf = []
            result.append(_Token("<"))
        elif (
            not quoted
            and char == ">"
            and buf
            and not re.fullmatch(r"(?:size|dm|datemodified|date-modified):", "".join(buf), re.I)
        ):
            result.append(_Token("".join(buf), has_quote, literal_start))
            result.append(_Token(">"))
            buf, has_quote, literal_start = [], False, None
        else:
            buf.append(char)
    if quoted:
        raise QueryError("Unclosed quote")
    if buf or has_quote:
        result.append(_Token("".join(buf), has_quote, literal_start))
    if len(result) > 256:
        raise QueryError("Query exceeds the 256-term limit")
    return result


def _combine(op: str, fragments: list[CompiledQuery]) -> CompiledQuery:
    if not fragments:
        return CompiledQuery("1", ())
    return CompiledQuery(
        "(" + f" {op} ".join(f.sql for f in fragments) + ")",
        tuple(p for f in fragments for p in f.parameters),
        any(f.uses_candidates for f in fragments),
    )


def compile_query(
    query: str, options: QueryOptions | None = None, *, now: datetime | None = None
) -> CompiledQuery:
    if len(query) > 8192:
        raise QueryError("Query exceeds the 8192-character limit")
    opts = options or QueryOptions()
    if opts.regex:
        return _text(query, opts, force_regex=True)
    tokens = tokenize(query)
    position = 0

    def atom(depth: int = 0) -> CompiledQuery:
        nonlocal position
        if depth > 32:
            raise QueryError("Query grouping exceeds 32 levels")
        if position >= len(tokens):
            raise QueryError("Missing search term after operator")
        token = tokens[position]
        position += 1
        if token.value == "<" and not token.quoted:
            value = conjunction(depth + 1)
            if position >= len(tokens) or tokens[position].value != ">":
                raise QueryError("Unclosed <group>")
            position += 1
            return value
        if token.value.upper() in {"|", ">", "OR", "AND"} and not token.quoted:
            raise QueryError(f"Unexpected operator: {token.value}")
        if token.value.upper() == "NOT" and not token.quoted:
            inner = atom(depth)
            return CompiledQuery(f"NOT ({inner.sql})", inner.parameters, inner.uses_candidates)
        value = token.value
        negate = False
        consumed = 0
        while value.startswith("!") and (
            token.literal_start is None or consumed < token.literal_start
        ):
            negate = not negate
            value = value[1:]
            consumed += 1
        if not value and token.value:
            inner = atom(depth)
        else:
            inner = (
                _text(value, opts)
                if token.literal_start == consumed
                else _term(value, opts, now or datetime.now())
            )
        return (
            CompiledQuery(f"NOT ({inner.sql})", inner.parameters, inner.uses_candidates)
            if negate
            else inner
        )

    def disjunction(depth: int) -> CompiledQuery:
        nonlocal position
        values = [atom(depth)]
        while (
            position < len(tokens)
            and not tokens[position].quoted
            and tokens[position].value.upper() in {"|", "OR"}
        ):
            position += 1
            values.append(atom(depth))
        return _combine("OR", values)

    def conjunction(depth: int = 0) -> CompiledQuery:
        nonlocal position
        values = []
        while position < len(tokens) and not (
            tokens[position].value == ">" and not tokens[position].quoted
        ):
            if not tokens[position].quoted and tokens[position].value.upper() == "AND":
                if not values:
                    raise QueryError("AND requires a preceding term")
                position += 1
                if position >= len(tokens):
                    raise QueryError("AND requires a following term")
            values.append(disjunction(depth))
        return _combine("AND", values)

    compiled = conjunction()
    if position != len(tokens):
        raise QueryError("Unexpected closing >")
    return compiled


def _term(value: str, options: QueryOptions, now: datetime) -> CompiledQuery:
    # Drive letters are paths, not function names.
    if ":" not in value or re.match(r"^[A-Za-z]:(?:[\\/]|$)", value):
        return _text(value, options)
    function, argument = value.split(":", 1)
    function = function.lower()
    modifiers = {
        "case": ("match_case", True),
        "nocase": ("match_case", False),
        "ww": ("match_whole_word", True),
        "noww": ("match_whole_word", False),
        "nopath": ("match_path", False),
    }
    if function in modifiers:
        attr, state = modifiers[function]
        return _term(argument, replace(options, **{attr: state}), now)
    if function in {"regex", "noregex"}:
        return _text(argument, options, force_regex=function == "regex")
    if function in {"file", "folder"}:
        flag = CompiledQuery("e.is_directory = ?", (int(function == "folder"),))
        return _combine("AND", [flag, _term(argument, options, now)]) if argument else flag
    if function in {"path", "name"}:
        return _text(argument, replace(options, match_path=function == "path"))
    if function == "parent":
        column = "parent" if options.match_case else "parent_fold"
        term = argument if options.match_case else argument.casefold()
        # The DB stores native normalized paths; accept both separators on Windows.
        term = os.path.normpath(term)
        return CompiledQuery(f"e.{column} = ?", (term,))
    if function == "ext":
        extensions = [v.lstrip(".") for v in argument.split(";") if v]
        if not extensions:
            return CompiledQuery("e.extension_fold = '' AND e.is_directory = 0", ())
        column = "extension" if options.match_case else "extension_fold"
        if not options.match_case:
            extensions = [e.casefold() for e in extensions]
        return CompiledQuery(
            f"e.is_directory = 0 AND e.{column} IN ({','.join('?' for _ in extensions)})",
            tuple(extensions),
        )
    if function == "size":
        return _combine(
            "AND", [CompiledQuery("e.is_directory = 0", ()), _comparison("size", argument, _size)]
        )
    if function in {"dm", "datemodified", "date-modified"}:
        return _date(argument, now)
    raise QueryError(
        f"Unsupported query function '{function}:'. Supported: ext, path, parent, name, file, folder, size, dm, datemodified, case, nocase, ww, noww, regex"
    )


def _text(value: str, options: QueryOptions, *, force_regex: bool = False) -> CompiledQuery:
    base = (
        "full_path"
        if options.match_path
        or (not force_regex and ("\\" in value or "/" in value or re.match(r"^[A-Za-z]:", value)))
        else "name"
    )
    if base == "full_path" and not force_regex and os.name == "nt":
        value = value.replace("/", "\\")
    column = base if options.match_case else base + "_fold"
    if force_regex or options.match_whole_word:
        import regex

        whole_value = value if options.match_case else value.casefold()
        pattern = value if force_regex else rf"(?<!\w){regex.escape(whole_value)}(?!\w)"
        flags = 0 if options.match_case or not force_regex else regex.IGNORECASE | regex.FULLCASE
        try:
            regex.compile(pattern, flags)
        except regex.error as exc:
            raise QueryError(f"Invalid regular expression: {exc}") from exc
        checked_column = base if force_regex else column
        check = CompiledQuery("safe_regex(?, e." + checked_column + ", ?) = 1", (pattern, flags))
        if force_regex:
            return check
        return (
            _combine("AND", [_candidate(value, base), check]) if options.use_candidates else check
        )
    term = value if options.match_case else value.casefold()
    if "*" in term or "?" in term:
        # SQL LIKE executes in native SQLite with case_sensitive_like=ON.
        pattern = (
            term.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
            .replace("*", "%")
            .replace("?", "_")
        )
        check = CompiledQuery(f"e.{column} LIKE ? ESCAPE '\\'", (pattern,))
        literal = max(re.split(r"[*?]", value), key=len, default="")
    else:
        check = CompiledQuery(f"instr(e.{column}, ?) > 0", (term,))
        literal = value
    return _combine("AND", [_candidate(literal, base), check]) if options.use_candidates else check


def _candidate(value: str, base: str) -> CompiledQuery:
    folded = value.casefold()
    if not folded or base != "name":
        return CompiledQuery("1", ())
    return CompiledQuery(
        "e.id IN (SELECT value FROM json_each(filename_candidates(?)))",
        (folded,),
        True,
    )


def _size(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgtpe]?i?b?)?\s*", value, re.I)
    if not match:
        raise QueryError(f"Invalid size: {value!r}")
    unit = (match[2] or "").lower().replace("i", "").rstrip("b")
    return int(float(match[1]) * 1024 ** ("kmgtpe".index(unit) + 1 if unit else 0))


def _comparison(column: str, value: str, convert) -> CompiledQuery:
    if ".." in value:
        lower, upper = value.split("..", 1)
        parts = []
        if lower:
            parts.append(CompiledQuery(f"e.{column} >= ?", (convert(lower),)))
        if upper:
            parts.append(CompiledQuery(f"e.{column} <= ?", (convert(upper),)))
        if not parts:
            raise QueryError("An empty range is not valid")
        return _combine("AND", parts)
    match = re.fullmatch(r"(>=|<=|!=|>|<|=)?(.+)", value)
    if not match:
        raise QueryError(f"Missing {column} comparison value")
    return CompiledQuery(f"e.{column} {match[1] or '='} ?", (convert(match[2]),))


def _date(value: str, now: datetime) -> CompiledQuery:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    key = value.lower()
    if key in {"today", "yesterday"}:
        start = today - timedelta(days=int(key == "yesterday"))
        return CompiledQuery(
            "e.modified >= ? AND e.modified < ?",
            (start.timestamp(), (start + timedelta(days=1)).timestamp()),
        )
    relative = re.fullmatch(r"last(\d+)(mins?|minutes?|hours?|days?|weeks?|months?|years?)", key)
    if relative:
        number = int(relative[1])
        unit = relative[2]
        seconds = (
            60
            if unit.startswith("min")
            else 3600
            if unit.startswith("hour")
            else 86400
            * (
                7
                if unit.startswith("week")
                else 30
                if unit.startswith("month")
                else 365
                if unit.startswith("year")
                else 1
            )
        )
        try:
            if unit.startswith(("month", "year")):
                months = number * (12 if unit.startswith("year") else 1)
                year, month = divmod(now.year * 12 + now.month - 1 - months, 12)
                start = now.replace(
                    year=year, month=month + 1, day=min(now.day, monthrange(year, month + 1)[1])
                )
            else:
                start = now - timedelta(seconds=number * seconds)
        except (ValueError, OverflowError) as exc:
            raise QueryError("Relative date is outside the supported calendar range") from exc
        return CompiledQuery(
            "e.modified >= ? AND e.modified <= ?",
            (start.timestamp(), now.timestamp()),
        )

    def date_number(text: str) -> float:
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError as exc:
            raise QueryError(
                f"Invalid date {text!r}; use YYYY-MM-DD, today, yesterday, or last7days"
            ) from exc

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        start = date_number(value)
        return CompiledQuery(
            "e.modified >= ? AND e.modified < ?",
            (start, (datetime.fromisoformat(value) + timedelta(days=1)).timestamp()),
        )
    if ".." in value:
        lower, upper = value.split("..", 1)
        parts = []
        if lower:
            parts.append(CompiledQuery("e.modified >= ?", (date_number(lower),)))
        if upper:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", upper):
                end = datetime.fromtimestamp(date_number(upper)) + timedelta(days=1)
                parts.append(CompiledQuery("e.modified < ?", (end.timestamp(),)))
            else:
                parts.append(CompiledQuery("e.modified <= ?", (date_number(upper),)))
        if not parts:
            raise QueryError("An empty date range is not valid")
        return _combine("AND", parts)
    return _comparison("modified", value, date_number)

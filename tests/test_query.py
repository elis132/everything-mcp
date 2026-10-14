"""Grammar/compiler tests, independent of any installed Everything program."""

from datetime import datetime

import pytest

from everything_mcp.query import QueryError, QueryOptions, compile_query


def test_substring_uses_packed_candidates_and_parameterized_exact_check():
    compiled = compile_query("annual report")
    assert compiled.uses_candidates
    assert "filename_candidates(?)" in compiled.sql
    assert "annual" not in compiled.sql
    assert "annual" in compiled.parameters


def test_short_query_uses_substring_candidates_and_exact_check():
    compiled = compile_query("ab")
    assert compiled.uses_candidates
    assert "instr(e.name_fold, ?)" in compiled.sql


def test_or_binds_more_tightly_than_and():
    compiled = compile_query("a b|c")
    assert " AND " in compiled.sql
    assert compiled.sql.index(" AND ") < compiled.sql.index(" OR ")
    assert compile_query("a OR b c").parameters[-1] == "c"


@pytest.mark.parametrize(
    "query",
    [
        '"unfinished',
        "a |",
        "| b",
        "a AND",
        "<a",
        "a>",
        "content:password",
        "width:500",
        "size:potato",
        "dm:banana",
    ],
)
def test_invalid_or_unsupported_queries_are_explicit(query):
    with pytest.raises(QueryError):
        compile_query(query)


def test_quotes_and_modifiers():
    result = compile_query('case:"Annual Report"')
    assert "instr(e.name, ?)" in result.sql
    assert result.parameters[-1] == "Annual Report"
    assert compile_query('"OR"').parameters[-1] == "or"


def test_negated_group_and_metadata():
    compiled = compile_query("!<ext:pdf|ext:docx> size:>=1mb")
    assert "NOT" in compiled.sql
    assert 1048576 in compiled.parameters


def test_sql_injection_is_data():
    result = compile_query('"a\' OR 1=1 --"')
    assert "1=1" not in result.sql
    assert result.parameters[-1] == "a' or 1=1 --"


def test_date_ranges_and_relative():
    now = datetime(2026, 10, 12, 15, 30)
    today = compile_query("dm:today", now=now)
    assert today.parameters == (
        datetime(2026, 10, 12).timestamp(),
        datetime(2026, 10, 13).timestamp(),
    )
    recent = compile_query("dm:last7days", now=now)
    assert recent.parameters[1] - recent.parameters[0] == 7 * 86400


def test_invalid_regex_checked_before_database_execution():
    with pytest.raises(QueryError, match="regular expression"):
        compile_query("[", QueryOptions(regex=True))


def test_wildcard_candidates_use_longest_literal():
    compiled = compile_query("*report?.pdf")
    assert "report" in compiled.parameters
    assert "%report_.pdf" in compiled.parameters


def test_windows_drive_path_is_not_unknown_function():
    compiled = compile_query(r"C:\Users\Alice")
    assert "full_path_fold" in compiled.sql
    assert "full_path_fold" in compile_query("C:").sql


def test_date_range_includes_the_complete_upper_date():
    compiled = compile_query("dm:2026-10-01..2026-10-12")
    assert compiled.parameters == (
        datetime(2026, 10, 1).timestamp(),
        datetime(2026, 10, 13).timestamp(),
    )
    assert "e.modified < ?" in compiled.sql


def test_relative_calendar_month_clamps_end_of_month():
    now = datetime(2026, 3, 31, 12)
    compiled = compile_query("dm:last1months", now=now)
    assert compiled.parameters[0] == datetime(2026, 2, 28, 12).timestamp()


def test_leading_quoted_bang_and_function_are_literal():
    bang = compile_query('"!important"')
    assert "NOT" not in bang.sql
    assert bang.parameters[-1] == "!important"
    function = compile_query('"ext:pdf"')
    assert "extension_fold" not in function.sql
    assert function.parameters[-1] == "ext:pdf"


def test_bang_outside_quotes_still_negates_literal_phrase():
    phrase = compile_query('!"important phrase"')
    assert "NOT" in phrase.sql
    assert phrase.parameters[-1] == "important phrase"
    bang = compile_query('!"!important"')
    assert "NOT" in bang.sql
    assert bang.parameters[-1] == "!important"
    assert "NOT" in compile_query("!important").sql

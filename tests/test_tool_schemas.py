"""Chat tool schemas must survive every provider's validator.

Google's Gemini API rejects a function declaration whose enum contains an
empty string (`INVALID_ARGUMENT: enum[0]: cannot be empty`), which broke the
whole chat tab for Gemini models -- litellm forwards the schemas verbatim, so
one bad enum takes down every turn, not just calls to that tool. OpenAI and
Anthropic tolerate the empty string, which is how it went unnoticed.

These tests pin the provider-portable subset: every enum value non-empty, and
the "reopen" spelling that replaced `""` still reaching the web endpoint as
the empty string it expects.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from jobd.tools.registry import build_tools


def _tools() -> list[Any]:
    # `conn` is only captured by handler closures; building the list never
    # touches it, so schema tests need no database.
    return build_tools(
        conn=object(),  # type: ignore[arg-type]
        self_address=None,
        max_messages=5,
        max_chars=2000,
    )


def _enums(schema: Any, path: str = "") -> list[tuple[str, list[Any]]]:
    found = []
    if isinstance(schema, dict):
        if "enum" in schema:
            found.append((path, schema["enum"]))
        for key, value in schema.items():
            found.extend(_enums(value, f"{path}.{key}"))
    elif isinstance(schema, list):
        for n, value in enumerate(schema):
            found.extend(_enums(value, f"{path}[{n}]"))
    return found


def test_no_enum_value_is_empty() -> None:
    for tool in _tools():
        for path, values in _enums(tool.parameters, tool.name):
            for value in values:
                assert isinstance(value, str) and value, (
                    f"{path}: enum value {value!r} would be rejected by the"
                    " Gemini API and kill every chat turn on those models"
                )


def test_reopen_maps_to_the_empty_outcome_the_endpoint_expects() -> None:
    """`/application/{id}/close` reads `""` as "reopen"; the tool schema now
    spells it `"reopen"`, so the handler must translate."""
    tool = next(
        t for t in _tools() if t.name == "propose_set_application_outcome"
    )
    assert "reopen" in tool.parameters["properties"]["outcome"]["enum"]

    proposal = tool.handler(application_id=str(uuid4()), outcome="reopen")
    assert proposal.fields["outcome"] == ""
    assert "reopen" in proposal.summary.lower()

    closed = tool.handler(application_id=str(uuid4()), outcome="rejected")
    assert closed.fields["outcome"] == "rejected"

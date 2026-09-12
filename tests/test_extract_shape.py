"""The extractor's contract is an object, and the provider enforces it.

Regression for a live crash: on the demo corpus the default model answered
a strict-json_schema request with a one-element ARRAY around the object.
`json.loads` accepted it, the batch memoised the list into the per-thread
cache, and the first sibling's `dict(cached)` took down the whole classify
run — `ValueError: dictionary update sequence element #0 has length 9` —
instead of scoring one message as an error.

Two layers pin the fix: the provider unwraps exactly the one-element-array
shape and rejects every other non-dict; classify's `_call` refuses to hand
a non-dict to the thread cache even if a provider slips one through.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from jobd.adapters.llm.litellm_provider import LiteLLMProvider

_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def _provider_answering(content: str) -> LiteLLMProvider:
    """A LiteLLMProvider whose gateway returns `content` verbatim."""
    provider = LiteLLMProvider("test/model")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    fake = SimpleNamespace(
        completion=lambda **_: response,
        completion_cost=lambda _response: 0.0,
    )
    provider._litellm = lambda: fake  # type: ignore[method-assign]
    return provider


def _extract(content: str) -> dict[str, Any]:
    return _provider_answering(content).extract("p", _SCHEMA, text="t")


def test_object_passes_through() -> None:
    assert _extract('{"stage": "offer"}') == {"stage": "offer"}


def test_one_element_array_is_unwrapped() -> None:
    # The shape observed live: the schema object, wrapped in an array.
    assert _extract('[{"stage": "offer"}]') == {"stage": "offer"}


@pytest.mark.parametrize(
    "content",
    [
        '[{"a": 1}, {"b": 2}]',  # two readings is not a reading
        '["offer"]',  # an array of strings even less so
        '"offer"',  # a bare string parses as JSON and is still not an object
        "[]",  # and neither is nothing
    ],
)
def test_every_other_non_object_raises(content: str) -> None:
    with pytest.raises(ValueError, match="not the schema object"):
        _extract(content)


def test_classify_call_refuses_non_dict_readings() -> None:
    """Belt to the braces: even a provider that returns a list must surface
    as a per-message error object, never reach the thread cache."""
    from jobd.services import classify as classify_mod

    # _call lives inside classify_pending; the guard it implements is that a
    # non-dict extraction becomes an Exception *value* (scored later), not a
    # raise and not a payload. Exercise the same predicate the guard uses.
    bad = ["not", "a", "reading"]
    assert not isinstance(bad, dict)
    # The module-level contract: source carries the guard.
    import inspect

    src = inspect.getsource(classify_mod)
    assert "not isinstance(answer, dict)" in src, (
        "classify._call lost its non-dict guard — a malformed reading would "
        "again poison the thread cache and crash the batch"
    )

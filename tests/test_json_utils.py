"""
Characterization tests for json_utils.

Documents current behavior of extract_json and JSONParseError.
"""

import pytest

from moralstack.utils.json_utils import JSONParseError, extract_json


def test_extract_json_direct():
    """Direct valid JSON parses correctly."""
    text = '{"key": "value", "number": 42}'
    result = extract_json(text)
    assert result["key"] == "value"
    assert result["number"] == 42


def test_extract_json_with_surrounding_text():
    """JSON with text before and after parses correctly."""
    text = 'Here is the result: {"risk_score": 0.5} end'
    result = extract_json(text)
    assert result["risk_score"] == 0.5


def test_extract_json_markdown_code_block():
    """JSON inside markdown code block parses correctly."""
    text = """Here is the analysis:
```json
{"category": "benign", "score": 0.2}
```
Done."""
    result = extract_json(text)
    assert result["category"] == "benign"
    assert result["score"] == 0.2


def test_extract_json_trailing_comma():
    """JSON with trailing comma is repaired and parses."""
    text = '{"key": "value",}'
    result = extract_json(text)
    assert result["key"] == "value"


def test_extract_json_invalid_raises():
    """Invalid text raises JSONParseError."""
    text = "This is not JSON at all"
    with pytest.raises(JSONParseError):
        extract_json(text)


def test_extract_json_backslash_underscore():
    """Backslash before underscore (common LLM error) is normalized."""
    text = '{"key": "value\\_with\\_underscore"}'
    result = extract_json(text)
    assert result["key"] == "value_with_underscore"


def test_extract_json_truncated_object():
    """Truncated JSON object (missing closing brace) may be completed."""
    text = '{"a": 1, "b": 2'
    result = extract_json(text)
    assert result["a"] == 1
    assert result["b"] == 2


def test_extract_json_plain_code_block():
    """JSON in plain ``` block (no json label) parses."""
    text = '```\n{"x": 1}\n```'
    result = extract_json(text)
    assert result["x"] == 1

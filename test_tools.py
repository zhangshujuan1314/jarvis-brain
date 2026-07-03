"""Unit tests for tools module."""
import asyncio
import json
import pytest
from tools import _calculate, _get_datetime, execute


class TestCalculate:
    def test_basic_math(self):
        result = json.loads(_calculate("2 + 3"))
        assert result["result"] == 5

    def test_complex_expression(self):
        result = json.loads(_calculate("2**10"))
        assert result["result"] == 1024

    def test_functions(self):
        result = json.loads(_calculate("sqrt(144)"))
        assert result["result"] == 12.0

    def test_trig(self):
        result = json.loads(_calculate("sin(pi/2)"))
        assert abs(result["result"] - 1.0) < 1e-10

    def test_empty_expression(self):
        result = json.loads(_calculate(""))
        assert "error" in result

    def test_invalid_chars(self):
        result = json.loads(_calculate("import os"))
        assert "error" in result

    def test_dunder_blocked(self):
        result = json.loads(_calculate("__import__('os')"))
        assert "error" in result

    def test_eval_blocked(self):
        result = json.loads(_calculate("eval('1+1')"))
        assert "error" in result


class TestGetDatetime:
    def test_default_timezone(self):
        result = json.loads(_get_datetime())
        assert "date" in result
        assert "time" in result
        assert "weekday" in result
        assert "UTC+8" in result["timezone"]

    def test_custom_timezone(self):
        result = json.loads(_get_datetime(0))
        assert "UTC+0" in result["timezone"]


class TestExecute:
    def test_unknown_tool(self):
        result = asyncio.run(execute("nonexistent", {}))
        parsed = json.loads(result)
        assert "error" in parsed

    def test_calculator(self):
        result = asyncio.run(execute("calculate", {"expression": "1+1"}))
        parsed = json.loads(result)
        assert parsed["result"] == 2

    def test_datetime(self):
        result = asyncio.run(execute("get_datetime", {"timezone_offset": 8}))
        parsed = json.loads(result)
        assert "date" in parsed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

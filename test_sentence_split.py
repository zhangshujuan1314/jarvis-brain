"""Unit tests for sentence splitting in llm module."""
import pytest
from llm import _drain_sentences


class TestDrainSentences:
    def test_single_sentence(self):
        complete, leftover = _drain_sentences("今天天气不错。")
        assert len(complete) == 1
        assert "今天天气不错。" in complete[0]
        assert leftover == ""

    def test_two_sentences(self):
        complete, leftover = _drain_sentences("今天天气不错。适合出门。")
        assert len(complete) == 2
        assert leftover == ""

    def test_incomplete_sentence(self):
        complete, leftover = _drain_sentences("今天天气")
        assert len(complete) == 0
        assert leftover == "今天天气"

    def test_mixed(self):
        complete, leftover = _drain_sentences("今天天气不错。适合")
        assert len(complete) == 1
        assert "今天天气不错。" in complete[0]
        assert leftover == "适合"

    def test_english_punctuation(self):
        complete, leftover = _drain_sentences("Hello world. How are you?")
        assert len(complete) == 2

    def test_exclamation(self):
        complete, leftover = _drain_sentences("太好了！")
        assert len(complete) == 1
        assert "太好了！" in complete[0]

    def test_empty(self):
        complete, leftover = _drain_sentences("")
        assert len(complete) == 0
        assert leftover == ""

    def test_only_punctuation(self):
        complete, leftover = _drain_sentences("。")
        assert len(complete) == 1

    def test_newline(self):
        complete, leftover = _drain_sentences("第一行\n第二行\n")
        assert len(complete) == 2

    def test_consecutive_punctuation(self):
        complete, leftover = _drain_sentences("真的吗？！")
        assert len(complete) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

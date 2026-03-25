"""P0 tests for agent.graph.nodes helper functions — pure logic, no DB."""
from __future__ import annotations

from unittest.mock import patch

from agent.graph.nodes import _count_tokens, _tool_sig, _truncate_history


class TestTruncateHistory:
    def test_within_budget_no_change(self):
        """History within budget is returned unchanged."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = _truncate_history(messages, budget_tokens=1000, model="openai/gpt-4o-mini")
        assert len(result) == 2

    def test_over_budget_drops_oldest(self):
        """Drops oldest messages first when over budget."""
        messages = [
            {"role": "user", "content": "message " * 100},
            {"role": "assistant", "content": "reply " * 100},
            {"role": "user", "content": "short"},
        ]
        result = _truncate_history(messages, budget_tokens=20, model="openai/gpt-4o-mini")
        # Should have dropped the long messages, keeping the short one
        assert len(result) < len(messages)
        assert result[-1]["content"] == "short"

    def test_empty_list(self):
        """Empty list returns empty list."""
        assert _truncate_history([], budget_tokens=100, model="openai/gpt-4o-mini") == []

    def test_single_message_over_budget(self):
        """Single message over budget → empty list (drops everything)."""
        messages = [{"role": "user", "content": "word " * 1000}]
        result = _truncate_history(messages, budget_tokens=10, model="openai/gpt-4o-mini")
        assert result == []


class TestCountTokens:
    def test_returns_positive_int(self):
        messages = [{"role": "user", "content": "Hello world"}]
        count = _count_tokens(messages, "openai/gpt-4o-mini")
        assert isinstance(count, int)
        assert count > 0

    def test_fallback_on_error(self):
        """Falls back to char/4 estimate when litellm fails."""
        messages = [{"role": "user", "content": "a" * 100}]
        with patch("litellm.token_counter", side_effect=Exception("fail")):
            count = _count_tokens(messages, "invalid/model")
        assert count == 25  # 100 chars / 4

    def test_empty_messages(self):
        """Empty message list returns 0 or small number."""
        count = _count_tokens([], "openai/gpt-4o-mini")
        assert count >= 0


class TestToolSig:
    def test_same_args_same_sig(self):
        """Identical tool + args produces identical signature."""
        sig1 = _tool_sig("web_read", {"url": "https://example.com"})
        sig2 = _tool_sig("web_read", {"url": "https://example.com"})
        assert sig1 == sig2

    def test_different_args_different_sig(self):
        """Different args produce different signatures."""
        sig1 = _tool_sig("web_read", {"url": "https://example.com"})
        sig2 = _tool_sig("web_read", {"url": "https://other.com"})
        assert sig1 != sig2

    def test_different_tools_different_sig(self):
        """Different tool names produce different signatures."""
        sig1 = _tool_sig("web_read", {"url": "https://example.com"})
        sig2 = _tool_sig("api_get", {"url": "https://example.com"})
        assert sig1 != sig2

    def test_run_skill_ignores_input(self):
        """run_skill dedup only uses skill_name, ignores free-text input."""
        sig1 = _tool_sig("run_skill", {"skill_name": "weather", "input": "what is the weather?"})
        sig2 = _tool_sig("run_skill", {"skill_name": "weather", "input": "tell me the weather forecast"})
        assert sig1 == sig2

    def test_run_skill_different_skills_differ(self):
        """Different skill names produce different signatures."""
        sig1 = _tool_sig("run_skill", {"skill_name": "weather"})
        sig2 = _tool_sig("run_skill", {"skill_name": "finance"})
        assert sig1 != sig2

    def test_chart_ignores_labels(self):
        """chart dedup only uses title, ignores labels/values."""
        sig1 = _tool_sig("chart", {"title": "Revenue", "labels": ["Q1"], "values": [100]})
        sig2 = _tool_sig("chart", {"title": "Revenue", "labels": ["Q1", "Q2"], "values": [100, 200]})
        assert sig1 == sig2

    def test_chart_different_titles_differ(self):
        """Different chart titles produce different signatures."""
        sig1 = _tool_sig("chart", {"title": "Revenue"})
        sig2 = _tool_sig("chart", {"title": "Profit"})
        assert sig1 != sig2

    def test_sig_format(self):
        """Signature has format 'tool_name|md5hash'."""
        sig = _tool_sig("web_read", {"url": "test"})
        parts = sig.split("|")
        assert len(parts) == 2
        assert parts[0] == "web_read"
        assert len(parts[1]) == 32  # MD5 hex digest length

    def test_empty_args(self):
        """Empty args dict doesn't crash."""
        sig = _tool_sig("web_read", {})
        assert "|" in sig

    def test_arg_order_irrelevant(self):
        """Argument key ordering doesn't affect the signature (sort_keys=True)."""
        sig1 = _tool_sig("api_get", {"url": "test", "headers": {"a": "b"}})
        sig2 = _tool_sig("api_get", {"headers": {"a": "b"}, "url": "test"})
        assert sig1 == sig2

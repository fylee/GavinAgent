"""Tests for Spec 027 — LLM Call Resilience."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import litellm
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_response(model="test-model"):
    resp = MagicMock()
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    resp.model = model
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# _is_retryable
# ══════════════════════════════════════════════════════════════════════════════

class TestIsRetryable:
    def test_rate_limit_is_retryable(self):
        from core.llm import _is_retryable
        assert _is_retryable(litellm.RateLimitError("429", llm_provider="", model="")) is True

    def test_service_unavailable_is_retryable(self):
        from core.llm import _is_retryable
        assert _is_retryable(litellm.ServiceUnavailableError("503", llm_provider="", model="")) is True

    def test_timeout_is_retryable(self):
        from core.llm import _is_retryable
        assert _is_retryable(litellm.Timeout("timeout", llm_provider="", model="")) is True

    def test_api_connection_error_is_retryable(self):
        from core.llm import _is_retryable
        assert _is_retryable(litellm.APIConnectionError("conn", llm_provider="", model="")) is True

    def test_auth_error_not_retryable(self):
        from core.llm import _is_retryable
        assert _is_retryable(litellm.AuthenticationError("401", llm_provider="", model="")) is False

    def test_bad_request_not_retryable(self):
        from core.llm import _is_retryable
        assert _is_retryable(litellm.BadRequestError("400", llm_provider="", model="")) is False


# ══════════════════════════════════════════════════════════════════════════════
# _jittered_backoff
# ══════════════════════════════════════════════════════════════════════════════

class TestJitteredBackoff:
    def test_attempt_0_within_range(self):
        from core.llm import _jittered_backoff
        for _ in range(50):
            v = _jittered_backoff(0, base=2.0, cap=30.0)
            assert 1.0 <= v <= 2.0

    def test_attempt_1_within_range(self):
        from core.llm import _jittered_backoff
        for _ in range(50):
            v = _jittered_backoff(1, base=2.0, cap=30.0)
            assert 2.0 <= v <= 4.0

    def test_cap_respected(self):
        from core.llm import _jittered_backoff
        for _ in range(50):
            v = _jittered_backoff(10, base=2.0, cap=30.0)
            assert v <= 30.0

    def test_values_not_all_identical(self):
        """Jitter produces different values each call."""
        from core.llm import _jittered_backoff
        values = {_jittered_backoff(0) for _ in range(20)}
        assert len(values) > 1


# ══════════════════════════════════════════════════════════════════════════════
# get_completion — retry
# ══════════════════════════════════════════════════════════════════════════════

class TestGetCompletionRetry:
    @patch("core.llm._jittered_backoff", return_value=0.0)
    @patch("core.llm.litellm.completion")
    def test_succeeds_on_first_attempt(self, mock_completion, _backoff, settings):
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = []
        settings.AGENT_LLM_MAX_RETRIES = 3
        settings.LLM_TIMEOUT_SECONDS = 30

        mock_completion.return_value = _make_response()
        from core.llm import get_completion
        get_completion([{"role": "user", "content": "hi"}])
        assert mock_completion.call_count == 1

    @patch("core.llm._jittered_backoff", return_value=0.0)
    @patch("core.llm.litellm.completion")
    def test_retries_on_rate_limit_then_succeeds(self, mock_completion, _backoff, settings):
        """429 on first 2 calls, success on 3rd."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = []
        settings.AGENT_LLM_MAX_RETRIES = 3
        settings.LLM_TIMEOUT_SECONDS = 30

        mock_completion.side_effect = [
            litellm.RateLimitError("429", llm_provider="", model=""),
            litellm.RateLimitError("429", llm_provider="", model=""),
            _make_response(),
        ]
        from core.llm import get_completion
        result = get_completion([{"role": "user", "content": "hi"}])
        assert mock_completion.call_count == 3
        assert result is not None

    @patch("core.llm._jittered_backoff", return_value=0.0)
    @patch("core.llm.litellm.completion")
    def test_raises_after_max_retries_exhausted(self, mock_completion, _backoff, settings):
        """Retries == max_retries, all fail → raises."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = []
        settings.AGENT_LLM_MAX_RETRIES = 3
        settings.LLM_TIMEOUT_SECONDS = 30

        mock_completion.side_effect = litellm.ServiceUnavailableError(
            "503", llm_provider="", model=""
        )
        from core.llm import get_completion
        with pytest.raises(litellm.ServiceUnavailableError):
            get_completion([{"role": "user", "content": "hi"}])
        assert mock_completion.call_count == 3

    @patch("core.llm.litellm.completion")
    def test_non_retryable_error_fails_immediately(self, mock_completion, settings):
        """401 → no retry, raises after 1 attempt."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = []
        settings.AGENT_LLM_MAX_RETRIES = 3
        settings.LLM_TIMEOUT_SECONDS = 30

        mock_completion.side_effect = litellm.AuthenticationError(
            "401", llm_provider="", model=""
        )
        from core.llm import get_completion
        with pytest.raises(litellm.AuthenticationError):
            get_completion([{"role": "user", "content": "hi"}])
        assert mock_completion.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# get_completion — fallback model chain
# ══════════════════════════════════════════════════════════════════════════════

class TestGetCompletionFallback:
    @patch("core.llm._jittered_backoff", return_value=0.0)
    @patch("core.llm.litellm.completion")
    def test_falls_back_to_secondary_model(self, mock_completion, _backoff, settings):
        """Primary exhausted → tries fallback model → succeeds."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = ["fallback-model"]
        settings.AGENT_LLM_MAX_RETRIES = 2
        settings.LLM_TIMEOUT_SECONDS = 30

        primary_error = litellm.ServiceUnavailableError("503", llm_provider="", model="")
        mock_completion.side_effect = [
            primary_error, primary_error,       # primary 2 attempts all fail
            _make_response("fallback-model"),   # fallback succeeds
        ]
        from core.llm import get_completion
        result = get_completion([{"role": "user", "content": "hi"}])
        assert result is not None
        assert mock_completion.call_count == 3
        assert mock_completion.call_args_list[-1][1]["model"] == "fallback-model"

    @patch("core.llm._jittered_backoff", return_value=0.0)
    @patch("core.llm.litellm.completion")
    def test_all_models_exhausted_raises(self, mock_completion, _backoff, settings):
        """All models in chain fail → raises last exception."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = ["fallback-a", "fallback-b"]
        settings.AGENT_LLM_MAX_RETRIES = 2
        settings.LLM_TIMEOUT_SECONDS = 30

        mock_completion.side_effect = litellm.ServiceUnavailableError(
            "503", llm_provider="", model=""
        )
        from core.llm import get_completion
        with pytest.raises(litellm.ServiceUnavailableError):
            get_completion([{"role": "user", "content": "hi"}])
        # 3 models × 2 retries = 6 total calls
        assert mock_completion.call_count == 6

    @patch("core.llm._jittered_backoff", return_value=0.0)
    @patch("core.llm.litellm.completion")
    def test_no_fallback_configured_behavior_unchanged(self, mock_completion, _backoff, settings):
        """No AGENT_FALLBACK_MODELS → same as before: retry primary only."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = []
        settings.AGENT_LLM_MAX_RETRIES = 3
        settings.LLM_TIMEOUT_SECONDS = 30

        mock_completion.side_effect = litellm.RateLimitError("429", llm_provider="", model="")
        from core.llm import get_completion
        with pytest.raises(litellm.RateLimitError):
            get_completion([{"role": "user", "content": "hi"}])
        assert mock_completion.call_count == 3

    @patch("core.llm._jittered_backoff", return_value=0.0)
    @patch("core.llm.litellm.completion")
    def test_non_retryable_on_fallback_stops_chain(self, mock_completion, _backoff, settings):
        """Non-retryable error on fallback model stops the chain immediately."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = ["fallback-model", "fallback-b"]
        settings.AGENT_LLM_MAX_RETRIES = 2
        settings.LLM_TIMEOUT_SECONDS = 30

        primary_error = litellm.ServiceUnavailableError("503", llm_provider="", model="")
        auth_error = litellm.AuthenticationError("401", llm_provider="", model="")
        mock_completion.side_effect = [
            primary_error, primary_error,  # primary exhausted
            auth_error,                    # fallback-model 401 → stop immediately
        ]
        from core.llm import get_completion
        with pytest.raises(litellm.AuthenticationError):
            get_completion([{"role": "user", "content": "hi"}])
        # fallback-b should NOT be tried
        assert mock_completion.call_count == 3


# ══════════════════════════════════════════════════════════════════════════════
# get_completion_stream — fallback (no retry)
# ══════════════════════════════════════════════════════════════════════════════

class TestGetCompletionStreamFallback:
    @patch("core.llm.litellm.completion")
    def test_stream_falls_back_on_retryable_error(self, mock_completion, settings):
        """Streaming: primary fails with retryable error → tries fallback once."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = ["fallback-model"]
        settings.LLM_TIMEOUT_SECONDS = 30

        primary_error = litellm.ServiceUnavailableError("503", llm_provider="", model="")
        fallback_gen = MagicMock()
        mock_completion.side_effect = [primary_error, fallback_gen]

        from core.llm import get_completion_stream
        result = get_completion_stream([{"role": "user", "content": "hi"}])
        assert result is fallback_gen
        assert mock_completion.call_count == 2

    @patch("core.llm.litellm.completion")
    def test_stream_non_retryable_raises_immediately(self, mock_completion, settings):
        """Streaming: non-retryable error → raises without trying fallback."""
        settings.LITELLM_DEFAULT_MODEL = "primary-model"
        settings.AGENT_FALLBACK_MODELS = ["fallback-model"]
        settings.LLM_TIMEOUT_SECONDS = 30

        mock_completion.side_effect = litellm.AuthenticationError(
            "401", llm_provider="", model=""
        )
        from core.llm import get_completion_stream
        with pytest.raises(litellm.AuthenticationError):
            get_completion_stream([{"role": "user", "content": "hi"}])
        assert mock_completion.call_count == 1

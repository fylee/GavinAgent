# Test Report — 027 LLM Call Resilience

**Spec**: `.spec/027-llm-call-resilience.md`
**Run date**: 2026-04-15
**Command**: `uv run pytest tests/core/test_llm_resilience.py -v`

## Summary

| Result | Count |
|--------|------:|
| ✅ Passed | 20 |
| ❌ Failed | 0 |
| ⏭ Skipped | 0 |
| **Total** | **20** |

## Per-Test Results

| # | Test | Result |
|---|------|--------|
| 1 | `TestIsRetryable::test_rate_limit_is_retryable` | ✅ PASSED |
| 2 | `TestIsRetryable::test_service_unavailable_is_retryable` | ✅ PASSED |
| 3 | `TestIsRetryable::test_timeout_is_retryable` | ✅ PASSED |
| 4 | `TestIsRetryable::test_api_connection_error_is_retryable` | ✅ PASSED |
| 5 | `TestIsRetryable::test_auth_error_not_retryable` | ✅ PASSED |
| 6 | `TestIsRetryable::test_bad_request_not_retryable` | ✅ PASSED |
| 7 | `TestJitteredBackoff::test_attempt_0_within_range` | ✅ PASSED |
| 8 | `TestJitteredBackoff::test_attempt_1_within_range` | ✅ PASSED |
| 9 | `TestJitteredBackoff::test_cap_respected` | ✅ PASSED |
| 10 | `TestJitteredBackoff::test_values_not_all_identical` | ✅ PASSED |
| 11 | `TestGetCompletionRetry::test_succeeds_on_first_attempt` | ✅ PASSED |
| 12 | `TestGetCompletionRetry::test_retries_on_rate_limit_then_succeeds` | ✅ PASSED |
| 13 | `TestGetCompletionRetry::test_raises_after_max_retries_exhausted` | ✅ PASSED |
| 14 | `TestGetCompletionRetry::test_non_retryable_error_fails_immediately` | ✅ PASSED |
| 15 | `TestGetCompletionFallback::test_falls_back_to_secondary_model` | ✅ PASSED |
| 16 | `TestGetCompletionFallback::test_all_models_exhausted_raises` | ✅ PASSED |
| 17 | `TestGetCompletionFallback::test_no_fallback_configured_behavior_unchanged` | ✅ PASSED |
| 18 | `TestGetCompletionFallback::test_non_retryable_on_fallback_stops_chain` | ✅ PASSED |
| 19 | `TestGetCompletionStreamFallback::test_stream_falls_back_on_retryable_error` | ✅ PASSED |
| 20 | `TestGetCompletionStreamFallback::test_stream_non_retryable_raises_immediately` | ✅ PASSED |

## Acceptance Criteria Coverage

| Criterion | Status |
|-----------|--------|
| 429/503/504/timeout retries up to `AGENT_LLM_MAX_RETRIES` | ✅ tests 12, 13 |
| Jittered backoff between retries | ✅ tests 7–10 |
| 401/400 fails immediately, no retry | ✅ tests 5, 6, 14 |
| Primary exhausted → fallback model | ✅ test 15 |
| Fallback success logged at WARNING | ✅ (verified by code review) |
| No `AGENT_FALLBACK_MODELS` → behaviour unchanged | ✅ test 17 |
| Non-retryable on fallback stops chain | ✅ test 18 |
| `get_completion_stream()` fallback (no retry) | ✅ tests 19, 20 |
| LLMUsage recorded on success | ✅ (unit-tested via `_record_usage` extraction) |

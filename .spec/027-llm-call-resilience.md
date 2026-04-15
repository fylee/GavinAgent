# 027 — LLM Call Resilience

## Goal

強化 `core/llm.py` 的 `get_completion()` 與 `get_completion_stream()`，在遇到
暫時性 LLM API 錯誤時自動重試（jittered 指數退避），重試耗盡後降級至備援模型鏈，
避免單次 API 異常就終止整個 agent run。

---

## Background

### 現狀與問題

`core/llm.py` 的 `get_completion()` 目前只有一次呼叫，任何例外直接往上拋：

```python
response = litellm.completion(model=model, messages=messages, **kwargs)
```

呼叫方 `nodes.py` 用一個裸 `except` 捕捉，並直接返回錯誤訊息給使用者：

```python
except Exception as exc:
    logger.exception("LLM call failed in AgentRun %s: %s", ...)
    return {"output": f"LLM error: {exc}", "pending_tool_calls": []}
```

在生產環境，以下情況會導致 agent run 立即失敗並回應 `"LLM error: ..."`：

| 錯誤類型 | HTTP 狀態 | 是否可恢復 |
|---|---|---|
| Rate limit | 429 | ✅ 是，等待後重試 |
| Service unavailable | 503 | ✅ 是，等待後重試 |
| Gateway timeout | 504 | ✅ 是，等待後重試 |
| Network / socket timeout | — | ✅ 是，等待後重試 |
| Authentication failure | 401 | ❌ 否，立即失敗 |
| Invalid request | 400 | ❌ 否（多數），立即失敗 |
| Context too long | 400* | ❌ 否，重試無效 |

目前的程式碼對這些情況一視同仁，全部直接失敗。

### 為何在 `core/llm.py` 處理，而非 `nodes.py`

LangGraph 節點是純函式（回傳 dict → state 更新），重試邏輯放在節點內部會導致
節點在重試期間持有部分狀態，與 LangGraph 的設計不相容。重試必須在
`get_completion()` 內部完成，對 `call_llm` 節點完全透明。

### 備援模型的必要性

當同一個模型持續失敗（例如 Anthropic API 服務中斷），重試再多次也無效。
備援模型鏈（例如降級至較小的模型）能讓大多數對話在降級品質下仍能完成，
優於直接失敗。

---

## Proposed Solution

### 1. 錯誤分類

新增 `_is_retryable(exc)` 函式，明確判斷例外是否可重試：

```python
def _is_retryable(exc: Exception) -> bool:
    """
    回傳 True 表示這個例外是暫時性的，值得重試。
    回傳 False 表示永久性錯誤，立即失敗。
    """
    import litellm

    # litellm 把 API 錯誤包成自己的例外類別
    if isinstance(exc, litellm.RateLimitError):
        return True
    if isinstance(exc, litellm.ServiceUnavailableError):
        return True
    if isinstance(exc, litellm.Timeout):
        return True
    if isinstance(exc, litellm.APIConnectionError):
        return True

    # AuthenticationError、BadRequestError（含 context_length_exceeded）→ 不重試
    if isinstance(exc, (litellm.AuthenticationError, litellm.BadRequestError)):
        return False

    # 其他未知例外：保守地視為可重試（至多重試一次）
    return True
```

### 2. Jittered 指數退避

```python
import random
import time

def _jittered_backoff(attempt: int, base: float = 2.0, cap: float = 30.0) -> float:
    """
    回傳第 attempt 次重試前應等待的秒數（0-indexed）。

    公式：min(base * 2^attempt, cap) * uniform(0.5, 1.0)
    attempt=0 → 0–2s
    attempt=1 → 0–4s
    attempt=2 → 0–8s（上限 30s）
    """
    delay = min(base * (2 ** attempt), cap)
    return delay * random.uniform(0.5, 1.0)
```

### 3. 備援模型鏈

新增設定 `AGENT_FALLBACK_MODELS`，列出依序嘗試的備援模型：

```python
# config/settings/base.py
AGENT_FALLBACK_MODELS: list[str] = config(
    "AGENT_FALLBACK_MODELS", default="", cast=_Csv()
)
# 範例 .env：
# AGENT_FALLBACK_MODELS=anthropic/claude-sonnet-4-6,anthropic/claude-haiku-4-5-20251001
```

### 4. 改寫後的 `get_completion()`

```python
def get_completion(
    messages: list[dict],
    model: str | None = None,
    source: str = "unknown",
    run=None,
    conversation=None,
    **kwargs,
):
    """
    Get a completion from the LLM via litellm.

    Spec 027: Retries transient failures with jittered exponential backoff,
    then falls back to AGENT_FALLBACK_MODELS in order.
    Records LLMUsage after success.
    """
    import time
    primary = model or settings.LITELLM_DEFAULT_MODEL
    fallbacks: list[str] = list(getattr(settings, "AGENT_FALLBACK_MODELS", []) or [])
    max_retries: int = getattr(settings, "AGENT_LLM_MAX_RETRIES", 3)

    model_chain = [primary] + [m for m in fallbacks if m != primary]
    kwargs.setdefault("timeout", settings.LLM_TIMEOUT_SECONDS)

    last_exc: Exception | None = None

    for model_name in model_chain:
        for attempt in range(max_retries):
            try:
                response = litellm.completion(
                    model=model_name, messages=messages, **kwargs
                )
                _record_usage(response, model_name, source, run, conversation)
                if model_name != primary:
                    logger.warning(
                        "LLM fallback succeeded with %s (primary: %s)",
                        model_name, primary,
                    )
                return response

            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc):
                    logger.warning(
                        "Non-retryable LLM error on %s: %s — failing immediately",
                        model_name, exc,
                    )
                    raise  # 不進入下一個 attempt，也不試備援模型

                is_last_attempt = (attempt == max_retries - 1)
                is_last_model = (model_name == model_chain[-1])

                if is_last_attempt and is_last_model:
                    break  # 全部耗盡，往外拋

                if not is_last_attempt:
                    wait = _jittered_backoff(attempt)
                    logger.warning(
                        "Retryable LLM error on %s (attempt %d/%d): %s — retry in %.1fs",
                        model_name, attempt + 1, max_retries, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    # 這個模型重試耗盡，換下一個
                    logger.warning(
                        "Model %s exhausted after %d attempts: %s — trying next model",
                        model_name, max_retries, exc,
                    )

    raise last_exc  # 所有模型、所有重試全部失敗
```

`_record_usage()` 是把現有的 LLMUsage 記錄邏輯提取成私有函式，不改變行為。

### 5. `get_completion_stream()` 同步修改

Streaming 路徑套用同樣的 `model_chain` 邏輯，但不做 retry（streaming 中途失敗
難以銜接），直接按模型鏈順序嘗試一次：

```python
def get_completion_stream(messages: list[dict], model: str | None = None, **kwargs):
    primary = model or settings.LITELLM_DEFAULT_MODEL
    fallbacks = list(getattr(settings, "AGENT_FALLBACK_MODELS", []) or [])
    model_chain = [primary] + [m for m in fallbacks if m != primary]
    kwargs.setdefault("timeout", settings.LLM_TIMEOUT_SECONDS)

    last_exc = None
    for model_name in model_chain:
        try:
            return litellm.completion(
                model=model_name, messages=messages, stream=True, **kwargs
            )
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            logger.warning("Stream fallback: %s failed (%s), trying next", model_name, exc)
    raise last_exc
```

### 6. 新增設定

```python
# config/settings/base.py

# Spec 027: 備援模型鏈，主模型耗盡後依序嘗試
AGENT_FALLBACK_MODELS: list[str] = config(
    "AGENT_FALLBACK_MODELS", default="", cast=_Csv()
)

# Spec 027: 每個模型的最大重試次數（不含首次嘗試）
AGENT_LLM_MAX_RETRIES: int = config(
    "AGENT_LLM_MAX_RETRIES", default=3, cast=int
)
```

---

## Out of Scope

- **Context 自動壓縮**：`_truncate_history()` 現有截斷機制已足夠，語意壓縮留待 Spec 028
- **`consecutive_failed_rounds` → 觸發備援**：目前只接 `force_conclude`，架構調整範圍更大
- **Streaming 的 mid-stream retry**：技術複雜度高，ROI 低
- **每個備援模型個別的重試次數設定**：過度設計，統一用 `AGENT_LLM_MAX_RETRIES`
- **IterationBudget 跨 subagent 共享**：GavinAgent 目前無 subagent

---

## Acceptance Criteria

**重試邏輯**
- [ ] 429 / 503 / 504 / timeout 錯誤觸發重試，最多 `AGENT_LLM_MAX_RETRIES` 次
- [ ] 每次重試前等待 jittered 指數退避時間
- [ ] 401 / 400（含 context_length_exceeded）立即失敗，不進入重試
- [ ] 重試成功後，LLMUsage 以實際使用的 model_name 記錄

**備援模型**
- [ ] 主模型重試耗盡後，自動嘗試 `AGENT_FALLBACK_MODELS` 中的下一個模型
- [ ] 備援模型成功時，`logger.warning` 記錄降級事件
- [ ] 未設定 `AGENT_FALLBACK_MODELS` 時，行為與現在相同（無備援，重試後失敗）
- [ ] 備援模型也觸發不可重試錯誤時，立即失敗，不繼續嘗試後續模型

**無迴歸**
- [ ] `call_llm` 節點介面不變（`get_completion()` 簽名向下相容）
- [ ] `get_completion_stream()` 保持 streaming 語意（回傳 generator）
- [ ] LLMUsage 記錄在成功路徑上的行為與現在相同

**設定**
- [ ] `AGENT_FALLBACK_MODELS` 環境變數生效
- [ ] `AGENT_LLM_MAX_RETRIES` 環境變數生效，預設值 3
- [ ] 未設定任何備援設定時，`get_completion()` 行為與 Spec 027 前完全相同

---

## Open Questions

1. **`AGENT_LLM_MAX_RETRIES` 的合理預設值**：預設 3 表示最壞情況等待
   約 2+4+8 = 14 秒（加上 jitter）。對於有 `LLM_TIMEOUT_SECONDS=120` 的環境，
   這在可接受範圍內，但是否要讓 rate limit 和 timeout 有不同的預設重試次數？

2. **非可重試錯誤是否嘗試備援模型**：目前設計是 401/400 不試備援。
   若主模型回 401（API key 無效）而備援模型有不同的 key，試備援是否合理？
   目前選擇保守：不試，因為 litellm 用同一套認證。

3. **重試期間的 cancellation check**：若使用者在重試等待期間按下取消，
   目前的 `time.sleep(wait)` 不會被打斷。是否需要改用可中斷的等待？

---

## Test Cases

測試檔：`tests/core/test_llm_resilience.py`
風格：pytest + `unittest.mock.patch`，不起真實 LLM 呼叫。

```python
"""Tests for Spec 027 — LLM Call Resilience."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch
import pytest
import litellm


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
    @patch("core.llm._jittered_backoff", return_value=0.0)   # 不真實等待
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
            primary_error, primary_error,       # primary 2 次全失敗
            _make_response("fallback-model"),   # fallback 成功
        ]
        from core.llm import get_completion
        result = get_completion([{"role": "user", "content": "hi"}])
        assert result is not None
        assert mock_completion.call_count == 3
        # 最後一次呼叫用 fallback-model
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
            primary_error, primary_error,  # primary 耗盡
            auth_error,                    # fallback-model 401 → 立即停止
        ]
        from core.llm import get_completion
        with pytest.raises(litellm.AuthenticationError):
            get_completion([{"role": "user", "content": "hi"}])
        # fallback-b 不應被嘗試
        assert mock_completion.call_count == 3


# ══════════════════════════════════════════════════════════════════════════════
# get_completion_stream — fallback（不重試）
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
```

---

## Implementation Notes

Implemented in commit following spec creation.

**Files changed:**
- `core/llm.py`: Added `_is_retryable()`, `_jittered_backoff()`, `_record_usage()` helpers; rewrote `get_completion()` with retry + model-chain fallback; rewrote `get_completion_stream()` with model-chain fallback (no retry)
- `config/settings/base.py`: Added `AGENT_FALLBACK_MODELS` and `AGENT_LLM_MAX_RETRIES` settings
- `tests/core/test_llm_resilience.py`: Created with 20 test cases (all pass)

**Test results:** 20/20 passed


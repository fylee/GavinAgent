from __future__ import annotations

import logging
import random
import time

from django.conf import settings
import litellm

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────


def _is_retryable(exc: Exception) -> bool:
    """Return True if exc is a transient error worth retrying."""
    if isinstance(exc, (litellm.RateLimitError, litellm.ServiceUnavailableError,
                        litellm.Timeout, litellm.APIConnectionError)):
        return True
    if isinstance(exc, (litellm.AuthenticationError, litellm.BadRequestError)):
        return False
    # Unknown exceptions: retry conservatively (at most max_retries times)
    return True


def _jittered_backoff(attempt: int, base: float = 2.0, cap: float = 30.0) -> float:
    """Return seconds to wait before attempt (0-indexed), capped and jittered.

    Formula: min(base * 2^attempt, cap) * uniform(0.5, 1.0)
    attempt=0 → 1–2 s | attempt=1 → 2–4 s | attempt=2 → 4–8 s (cap 30 s)
    """
    delay = min(base * (2 ** attempt), cap)
    return delay * random.uniform(0.5, 1.0)


def _record_usage(
    response,
    model: str,
    source: str,
    run=None,
    conversation=None,
) -> None:
    """Persist LLMUsage record after a successful completion. Never raises."""
    try:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        cost = 0.0
        try:
            cost = litellm.completion_cost(completion_response=response) or 0.0
        except Exception:
            pass
        from agent.models import LLMUsage
        LLMUsage.objects.create(
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
            estimated_cost_usd=cost,
            source=source,
            run=run,
            conversation=conversation,
        )
    except Exception:
        pass


# ── public API ──────────────────────────────────────────────────────────────


def get_completion(
    messages: list[dict],
    model: str | None = None,
    source: str = "unknown",
    run=None,
    conversation=None,
    **kwargs,
):
    """Get a completion from the LLM via litellm.

    Spec 027: retries transient failures with jittered exponential backoff,
    then falls back through AGENT_FALLBACK_MODELS.  Records LLMUsage on success.
    """
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
                    raise

                is_last_attempt = (attempt == max_retries - 1)
                is_last_model = (model_name == model_chain[-1])

                if is_last_attempt and is_last_model:
                    break  # all exhausted — fall through to raise

                if not is_last_attempt:
                    wait = _jittered_backoff(attempt)
                    logger.warning(
                        "Retryable LLM error on %s (attempt %d/%d): %s — retry in %.1fs",
                        model_name, attempt + 1, max_retries, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Model %s exhausted after %d attempts: %s — trying next model",
                        model_name, max_retries, exc,
                    )

    raise last_exc  # type: ignore[misc]


def get_completion_stream(
    messages: list[dict],
    model: str | None = None,
    source: str = "unknown",
    run=None,
    conversation=None,
    **kwargs,
):
    """Get a streaming completion via litellm.

    Spec 027: tries fallback models once (no retry) on retryable errors.
    Spec 030: accepts run/conversation/source for usage recording by caller.
    Non-retryable errors raise immediately without trying the fallback chain.
    """
    primary = model or settings.LITELLM_DEFAULT_MODEL
    fallbacks: list[str] = list(getattr(settings, "AGENT_FALLBACK_MODELS", []) or [])
    model_chain = [primary] + [m for m in fallbacks if m != primary]
    kwargs.setdefault("timeout", settings.LLM_TIMEOUT_SECONDS)

    last_exc: Exception | None = None
    for model_name in model_chain:
        try:
            return litellm.completion(
                model=model_name, messages=messages, stream=True, **kwargs
            )
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            logger.warning(
                "Stream LLM fallback: %s failed (%s) — trying next model",
                model_name, exc,
            )
    raise last_exc  # type: ignore[misc]


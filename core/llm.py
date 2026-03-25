from __future__ import annotations

from django.conf import settings
import litellm


def get_completion(
    messages: list[dict],
    model: str | None = None,
    source: str = "unknown",
    run=None,
    conversation=None,
    **kwargs,
):
    """Get a completion from the LLM via litellm. Records LLMUsage after success."""
    model = model or settings.LITELLM_DEFAULT_MODEL
    kwargs.setdefault("timeout", settings.LLM_TIMEOUT_SECONDS)
    response = litellm.completion(model=model, messages=messages, **kwargs)

    # Record usage
    try:
        usage = getattr(response, "usage", None)
        if usage:
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
        pass  # Never fail the caller due to usage recording

    return response


def get_completion_stream(messages: list[dict], model: str | None = None, **kwargs):
    """Get a streaming completion from the LLM via litellm."""
    model = model or settings.LITELLM_DEFAULT_MODEL
    kwargs.setdefault("timeout", settings.LLM_TIMEOUT_SECONDS)
    response = litellm.completion(model=model, messages=messages, stream=True, **kwargs)
    return response

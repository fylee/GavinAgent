from django.http import HttpRequest
from .base import InboundEvent, InterfaceType


def event_from_request(request: HttpRequest, conversation_id: str | None = None) -> InboundEvent:
    content = request.POST.get("content", "").strip()
    return InboundEvent(
        interface=InterfaceType.WEB,
        content=content,
        conversation_id=conversation_id,
        metadata={"path": request.path},
    )

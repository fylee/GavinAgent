import json
import logging
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View
from django.conf import settings
from .base import InboundEvent, InterfaceType

logger = logging.getLogger(__name__)


class TelegramWebhookView(View):
    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse(status=400)

        message = data.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()

        if not text or not chat_id:
            return JsonResponse({"ok": True})

        event = InboundEvent(
            interface=InterfaceType.TELEGRAM,
            content=text,
            external_id=chat_id,
        )
        self._handle(event, chat_id)
        return JsonResponse({"ok": True})

    def _handle(self, event: InboundEvent, chat_id: str):
        from chat.models import Conversation
        from chat.tasks import process_chat_message

        conversation, _ = Conversation.objects.get_or_create(
            interface=Conversation.Interface.TELEGRAM,
            external_id=chat_id,
            defaults={"title": f"Telegram {chat_id}"},
        )
        process_chat_message.delay(str(conversation.id), event.content)

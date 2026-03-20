from celery import shared_task


@shared_task(bind=True, max_retries=3)
def process_chat_message(self, conversation_id: str) -> str:
    from .models import Conversation
    from .services import ChatService
    try:
        conversation = Conversation.objects.get(id=conversation_id)
        service = ChatService(conversation)
        msg = service.reply()
        return str(msg.id)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2)

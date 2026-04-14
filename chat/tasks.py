from celery import shared_task


def _generate_conversation_title(conversation) -> None:
    """Generate a short LLM title from the first user message and save it."""
    from .models import Message

    # Only on the first user turn
    if Message.objects.filter(conversation=conversation, role=Message.Role.USER).count() != 1:
        return

    first_msg = Message.objects.filter(conversation=conversation, role=Message.Role.USER).first()
    if not first_msg:
        return

    try:
        from core.llm import get_completion

        response = get_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the user's message as a concise chat title "
                        "in 6 words or fewer. Reply with only the title — "
                        "no quotes, no trailing punctuation."
                    ),
                },
                {"role": "user", "content": first_msg.content[:500]},
            ],
            source="title_generation",
            max_tokens=20,
        )
        title = response.choices[0].message.content.strip().strip('"\'').rstrip(".")
        if title:
            conversation.title = title[:100]
            conversation.save(update_fields=["title", "updated_at"])
    except Exception:
        pass  # Keep existing placeholder title on any failure


@shared_task(bind=True, max_retries=3)
def process_chat_message(self, conversation_id: str) -> str:
    from .models import Conversation
    from .services import ChatService
    try:
        conversation = Conversation.objects.get(id=conversation_id)
        service = ChatService(conversation)
        msg = service.reply()
        _generate_conversation_title(conversation)
        return str(msg.id)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2)

from django.dispatch import Signal

# Fired by MessageCreateView after a user message is saved.
# kwargs: conversation_id (str), message_id (str)
message_created = Signal()

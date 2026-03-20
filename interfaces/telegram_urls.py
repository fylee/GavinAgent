from django.urls import path
from .telegram import TelegramWebhookView

urlpatterns = [
    path("webhook/", TelegramWebhookView.as_view(), name="telegram-webhook"),
]

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("chat/", include("chat.urls")),
    path("agent/", include("agent.urls")),
    path("integrations/telegram/", include("interfaces.telegram_urls")),
]

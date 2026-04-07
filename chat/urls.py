from django.urls import path
from . import views

app_name = "chat"
urlpatterns = [
    path("", views.ConversationListView.as_view(), name="list"),
    path("conversations/", views.ConversationCreateView.as_view(), name="create"),
    path("conversations/<uuid:pk>/", views.ConversationDetailView.as_view(), name="detail"),
    path("conversations/<uuid:pk>/update/", views.ConversationUpdateView.as_view(), name="update"),
    path("conversations/<uuid:pk>/agent/", views.ConversationAgentToggleView.as_view(), name="agent-toggle"),
    path("conversations/<uuid:pk>/cancel/", views.CancelRunView.as_view(), name="cancel-run"),
    path(
        "conversations/<uuid:conversation_pk>/messages/",
        views.MessageCreateView.as_view(),
        name="message-create",
    ),
    path(
        "conversations/<uuid:conversation_pk>/messages/<uuid:pk>/stream/",
        views.MessageStreamView.as_view(),
        name="message-stream",
    ),
    path("scheduled/<uuid:pk>/", views.WorkflowOutputView.as_view(), name="workflow-output"),
    path("scheduled/<uuid:pk>/poll/", views.WorkflowOutputPollView.as_view(), name="workflow-output-poll"),
]

from django.urls import path
from . import views

app_name = "agent"
urlpatterns = [
    # Dashboard
    path("", views.DashboardView.as_view(), name="dashboard"),

    # Runs
    path("runs/", views.RunListView.as_view(), name="list"),
    path("runs/create/", views.RunCreateView.as_view(), name="create"),
    path("runs/<uuid:pk>/", views.RunDetailView.as_view(), name="detail"),
    path("runs/<uuid:pk>/status/", views.RunStatusView.as_view(), name="status"),
    path("runs/<uuid:pk>/respond/", views.RunRespondView.as_view(), name="respond"),
    path("runs/<uuid:pk>/cancel/", views.RunCancelView.as_view(), name="cancel"),

    # Logs
    path("logs/", views.LogsView.as_view(), name="logs"),

    # Memory
    path("memory/", views.MemoryView.as_view(), name="memory"),
    path("memory/reembed/", views.MemoryReembedView.as_view(), name="memory-reembed"),
    path("memory/search/", views.MemorySearchView.as_view(), name="memory-search"),
    path("memory/paragraph/delete/", views.MemoryParagraphDeleteView.as_view(), name="memory-paragraph-delete"),
    path("memory/paragraph/edit/", views.MemoryParagraphEditView.as_view(), name="memory-paragraph-edit"),

    # Tools
    path("tools/", views.ToolsView.as_view(), name="tools"),
    path("tools/<str:name>/toggle/", views.ToolToggleView.as_view(), name="tool-toggle"),
    path("tools/<str:name>/policy/", views.ToolPolicyView.as_view(), name="tool-policy"),

    # Skills
    path("skills/", views.SkillsView.as_view(), name="skills"),
    path("skills/install/", views.SkillInstallView.as_view(), name="skill-install"),
    path("skills/<str:name>/toggle/", views.SkillToggleView.as_view(), name="skill-toggle"),
    path("skills/<str:name>/delete/", views.SkillDeleteView.as_view(), name="skill-delete"),

    # Agent CRUD
    path("agents/", views.AgentListView.as_view(), name="agent-list"),
    path("agents/create/", views.AgentCreateView.as_view(), name="agent-create"),
    path("agents/<uuid:pk>/", views.AgentEditView.as_view(), name="agent-edit"),
    path("agents/<uuid:pk>/delete/", views.AgentDeleteView.as_view(), name="agent-delete"),
    path("agents/<uuid:pk>/set-default/", views.AgentSetDefaultView.as_view(), name="agent-set-default"),

    # Tool approval
    path("approve/<uuid:tool_id>/", views.ToolApproveView.as_view(), name="approve"),

    # Monitoring
    path("monitoring/", views.MonitoringView.as_view(), name="monitoring"),
    path("monitoring/health/", views.HealthCheckView.as_view(), name="health"),

    # Workspace
    path("workspace/", views.WorkspaceFileListView.as_view(), name="workspace"),
    path("workspace/<str:filename>/", views.WorkspaceFileEditView.as_view(), name="workspace-file"),

    # Workspace file serving (charts, images)
    path("workspace-file/<str:filename>", views.WorkspaceFileServeView.as_view(), name="workspace-serve"),

    # Workflows
    path("workflows/", views.WorkflowListView.as_view(), name="workflow-list"),
    path("workflows/create/", views.WorkflowCreateView.as_view(), name="workflow-create"),
    path("workflows/reload/", views.WorkflowReloadView.as_view(), name="workflow-reload"),
    path("workflows/<uuid:pk>/", views.WorkflowDetailView.as_view(), name="workflow-detail"),
    path("workflows/<uuid:pk>/toggle/", views.WorkflowToggleView.as_view(), name="workflow-toggle"),
    path("workflows/<uuid:pk>/run-now/", views.WorkflowRunNowView.as_view(), name="workflow-run-now"),
    path("workflows/<uuid:pk>/save/", views.WorkflowSaveView.as_view(), name="workflow-save"),

    # MCP Servers
    path("mcp/", views.MCPServerListView.as_view(), name="mcp-list"),
    path("mcp/add/", views.MCPServerAddView.as_view(), name="mcp-add"),
    path("mcp/<uuid:pk>/", views.MCPServerDetailView.as_view(), name="mcp-detail"),
    path("mcp/<uuid:pk>/toggle/", views.MCPServerToggleView.as_view(), name="mcp-toggle"),
    path("mcp/<uuid:pk>/refresh/", views.MCPServerRefreshView.as_view(), name="mcp-refresh"),
    path("mcp/<uuid:pk>/delete/", views.MCPServerDeleteView.as_view(), name="mcp-delete"),
]

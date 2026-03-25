# 011 — Scheduled Workflow Output: Deferred Execution & Result Visibility

## Goal

When a user asks the agent to do something **at a specific time**, the agent should create a scheduled workflow and stop — not attempt to execute the task immediately. When the scheduled time arrives, the workflow runs, and its output is delivered as a new message that the user can find in the **chat UI** and in a dedicated **Workflows → Output** inbox.

---

## Background

### Current behaviour (broken)

The screenshot shows the agent responding to *"write a report at 15:20 today"* by:

1. Executing the task **immediately** (writing the report right now)
2. Replying in chat with the result inline

This is wrong for two reasons:

1. **Wrong time** — the user said 15:20; the task ran at whatever time the user sent the message.
2. **Wrong intent** — the user wanted a *scheduled job*, not an inline answer.

### Why it happens

- The agent picks up the `workflow-management` skill and writes a YAML file + calls `reload_workflows` ✓
- But it **also** proceeds to execute the task steps immediately in the same turn, because:
  - The `workflow-management` SKILL.md says "write the file and call `reload_workflows`" but does not say "do not answer the user's underlying question yourself"
  - The LLM conflates "create a scheduled workflow" with "and also do the task now"

### What the correct flow looks like

```
User: "write a report at 15:20 today"
  └─► Agent creates workflow YAML → reload_workflows → replies:
      "Done. I've scheduled a report for 15:20 today. You'll find the
       result in this conversation and in the Workflows inbox."

[15:20 — Celery Beat fires]
  └─► execute_workflow task runs
      └─► WorkflowRunner runs steps
          └─► Output delivered as new Message in the linked conversation
              └─► Visible in Chat UI (conversation thread)
              └─► Visible in Agent UI → Workflows → [workflow name] → Output tab
```

---

## Proposed Solution

### Part 1 — Agent behaviour fix (SKILL.md)

Add an explicit instruction to `workflow-management/SKILL.md`:

> **Do not perform the task yourself.** Your job is only to create the workflow file and confirm the schedule to the user. Never execute the underlying task steps inline. If you find yourself about to write a report, fetch a joke, or do anything task-related — stop. That work belongs to the scheduled workflow.

Add a **confirmation reply template** the agent must use after creating a workflow:

```
Scheduled ✓  
Workflow: <name>  
Runs: <human-readable schedule>  
Output will appear in this conversation and in the Workflows inbox.
```

---

### Part 2 — Conversation threading (already partially working)

The `_deliver()` function in `WorkflowRunner` already creates a `Message` in the linked conversation when `delivery == "announce"` and `workflow.conversation_id` is set.

**What's missing:**

1. The delivered message has no metadata marking it as workflow output — it looks identical to a regular assistant reply. We should stamp it with `metadata={"source": "workflow", "workflow_id": str(workflow.id), "workflow_name": workflow.name}` so the chat UI can render it differently (e.g. a small badge).

2. The conversation's `updated_at` is not bumped when a workflow delivers a message — the conversation won't float to the top of the sidebar.

3. If the workflow has no `conversation_id` (user never linked it), a dedicated **Workflows inbox conversation** is used (see Part 3).

**Changes to `WorkflowRunner._deliver()`:**

```python
if workflow.delivery == "announce":
    target_conversation = (
        workflow.conversation
        or _get_or_create_workflow_inbox()
    )
    Message.objects.create(
        conversation=target_conversation,
        role=Message.Role.ASSISTANT,
        content=output,
        metadata={
            "source": "workflow",
            "workflow_id": str(workflow.id),
            "workflow_name": workflow.name,
        },
    )
    # Bump conversation updated_at so it surfaces in sidebar
    target_conversation.save(update_fields=["updated_at"])
```

---

### Part 3 — Workflow inbox conversation

A single system conversation named **"Workflow Outputs"** acts as a catch-all for workflows that have no linked conversation.

- Created once, on first use (lazy `get_or_create`)
- `interface = "web"`, `title = "Workflow Outputs"`, `metadata = {"system": true, "workflow_inbox": true}`
- Appears in the chat sidebar under a **"Scheduled"** group (separate from Today/Yesterday/Older)
- Not deletable from the UI (guarded by the `system` metadata flag)

```python
def _get_or_create_workflow_inbox():
    from chat.models import Conversation
    inbox, _ = Conversation.objects.get_or_create(
        interface=Conversation.Interface.WEB,
        metadata__workflow_inbox=True,
        defaults={
            "title": "Workflow Outputs",
            "metadata": {"system": True, "workflow_inbox": True},
        },
    )
    return inbox
```

---

### Part 4 — Chat UI: workflow message badge

When rendering a `Message` whose `metadata.source == "workflow"`, the chat template shows a small pill above the message content:

```
┌─────────────────────────────────────────────┐
│ 🕐 Scheduled output · my-report-workflow    │
│ 2026-03-23 15:20                             │
├─────────────────────────────────────────────┤
│  Here is your report: ...                   │
└─────────────────────────────────────────────┘
```

Change required in `chat/templates/chat/_message.html`:
- If `message.metadata.source == "workflow"`, render the badge above the bubble.
- Badge links to the workflow detail page: `/agent/workflows/<workflow_id>/`

---

### Part 5 — Workflows UI: Output tab on workflow detail page

Add an **Output** tab to `agent/templates/agent/workflow_detail.html` alongside the existing YAML editor and Run History sections.

The tab shows all messages delivered by this workflow, newest first:

```
┌──────────────────────────────────────────────────────┐
│  Output                                              │
├──────────────────────────────────────────────────────┤
│  2026-03-23 15:20   [View in chat ↗]                 │
│  Here is your report: ...                            │
├──────────────────────────────────────────────────────┤
│  2026-03-22 09:00   [View in chat ↗]                 │
│  Here is yesterday's report: ...                     │
└──────────────────────────────────────────────────────┘
```

Implementation:
- Query: `Message.objects.filter(metadata__workflow_id=str(workflow.id)).order_by("-created_at")`
- "View in chat" links to `/chat/<conversation_id>/` with `#msg-<message_id>` anchor.

---

### Part 6 — Sidebar "Scheduled" group

`chat/views.py` `SidebarMixin.get_context_data()` currently groups conversations by date (Today/Yesterday/This Week/Older).

Add a **Scheduled** group at the top of the sidebar for conversations that have `metadata__workflow_inbox=True`:

```python
scheduled = [c for c in all_conversations if c.metadata.get("workflow_inbox")]
regular    = [c for c in all_conversations if not c.metadata.get("workflow_inbox")]
# then apply Today/Yesterday/etc grouping only to `regular`
```

The Scheduled group appears above Today with a calendar icon.

---

## Data Model Changes

No new migrations required. All changes use existing fields:

| Model | Change |
|-------|--------|
| `chat.Message` | `metadata` stamped with `source`, `workflow_id`, `workflow_name` — existing JSONField |
| `chat.Conversation` | `metadata` stamped with `workflow_inbox: true` for inbox conv — existing JSONField |

---

## Out of Scope

- Push notifications / WebSocket live updates when a scheduled message arrives (future spec)
- Email delivery of workflow output
- Multi-conversation fan-out (one workflow → multiple conversations)
- Workflow output approval before delivery

---

## Open Questions

1. **Inbox conversation visibility** — should "Workflow Outputs" be pinned at the very top of the sidebar, or just float naturally when new output arrives?
2. **Conversation badge in agent confirmation reply** — after the agent writes the workflow YAML, should it also post a user-visible "Scheduled ✓" message to the chat, or just respond naturally in its reply?
3. **One-shot cleanup** — for `at:` (one-shot) triggers, should the workflow be automatically deleted after it runs, or kept for audit purposes?
4. **Backfill** — existing workflow runs that already delivered messages have no `metadata.source`; should we backfill on migration?

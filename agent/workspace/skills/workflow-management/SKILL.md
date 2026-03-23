---
name: workflow-management
description: Creating, updating, and managing scheduled workflows
triggers: [create workflow, schedule, every monday, every day, every week, every hour, run automatically, set up a workflow, recurring task, automated task, remind me every, post every, check every, workflow, cron, scheduled]
version: 1
---

## Workflow Management

Workflows are YAML files stored in `workspace/workflows/`. Each workflow has a trigger (cron, interval, or one-shot), an agent assignment, steps, and a delivery mode.

### Creating a workflow

1. Write the workflow YAML using `file_write` to `workspace/workflows/<name>.yml`
2. Call `api_post` to `/agent/workflows/reload/` (no body required) to register it

Always include the current `conversation_id` in the YAML when delivery is `announce` (the default).

### Workflow YAML schema

```yaml
name: <slug>                      # required; unique; used as filename
description: <one-line summary>   # optional

agent: default                    # "default" or an agent name
enabled: true                     # true | false
delivery: announce                # announce | silent | telegram

trigger:
  cron: "0 9 * * 1"              # 5-field cron (minute hour dom month dow)
  timezone: Asia/Taipei           # optional; defaults to UTC

steps:
  - name: <step-name>
    prompt: >
      The full instruction for this step. Be specific.

  - name: <next-step>
    prompt: >
      This step receives the previous step's output as context automatically.
```

### Trigger types

**Recurring cron:**
```yaml
trigger:
  cron: "0 9 * * 1"    # 9am every Monday
  timezone: Asia/Taipei
```

**Fixed interval:**
```yaml
trigger:
  interval_minutes: 60  # every 60 minutes
```

**One-shot (runs once, then disables itself):**
```yaml
trigger:
  at: "2026-04-01T09:00:00+08:00"
```

### Delivery modes

- `announce` — posts the final step's output as an assistant message in the conversation that created the workflow. Requires `conversation` field.
- `silent` — saves output to the AgentRun record only; no notification.
- `telegram` — sends the final step's output via Telegram regardless of conversation.

### How to include the current conversation ID

When delivery is `announce`, include the conversation ID so the output is delivered to the right conversation:

```yaml
conversation: <current_conversation_id>
```

The current conversation ID is available in the system prompt as `current_conversation_id`. Use it directly.

### Step isolation

Each step runs as a separate agent run with a clean context. The previous step's output is automatically prepended to the next step's prompt:

```
Previous step output:
<output from step N-1>

Current step — <step name>:
<your prompt>
```

Design steps to be self-contained. Step 1 gathers data; step 2 formats/posts it.

### Example: weekly AI news digest

```yaml
name: weekly-ai-news
description: Search for AI news every Monday and post a digest
agent: default
enabled: true
delivery: announce
conversation: abc123...

trigger:
  cron: "0 9 * * 1"
  timezone: Asia/Taipei

steps:
  - name: search
    prompt: >
      Search for the latest AI and LLM news from the past 7 days.
      Return a structured list of the top 5 stories with title, source, and a one-sentence summary.

  - name: format-and-post
    prompt: >
      Format the news digest from the previous step into a clean, readable message.
      Post it to Telegram using the send_telegram tool.
```

### After creating or editing a workflow

Always call `api_post` to `/agent/workflows/reload/` so the scheduler picks up changes immediately.

# 009 — Workflow Scheduling

## Goal

Replace the single `HEARTBEAT.md` checklist with a structured workflow system that supports multiple independent schedules, explicit multi-step pipelines with true step isolation, per-workflow agent assignment, and creation from chat.

## Background

The current heartbeat system (`HEARTBEAT.md` + `heartbeat_task`) has significant limitations:

1. **One schedule for everything** — all tasks share a single 30-min interval
2. **Unstructured** — the agent interprets a markdown checklist; no explicit steps, branching, or conditions
3. **No per-task agents** — all heartbeat runs use the default agent
4. **No webhook triggers** — only time-based
5. **No chat integration** — tasks must be added by manually editing a file

The system needs a proper workflow abstraction, inspired by OpenClaw's workflow model:
- Each workflow is a YAML file defining its trigger, agent, and steps
- Multiple workflows run on independent schedules
- Each step runs as an isolated `AgentRun`, with output from the previous step passed as input to the next
- Workflows can be created from chat by the agent itself
- The existing `django_celery_beat` infrastructure serves as the scheduler backend

The `HEARTBEAT.md` mechanism is retired once this spec is implemented.

### Why true step isolation (not a single concatenated prompt)

The naive approach — concatenate all step prompts into one big prompt for a single `AgentRun` — has fundamental problems:

- **Context bloat**: each step's output accumulates in the context window; a 5-step pipeline with verbose outputs can hit token limits
- **Instruction bleed**: the agent conflates instructions from earlier steps with the current one
- **No retry granularity**: if step 3 fails, the entire workflow re-runs from step 1
- **No branching**: impossible to say "if step 1 found no results, skip step 2"

True step isolation runs each step as a separate `AgentRun`. The output of step N is passed as the input to step N+1. Each step starts with a clean context window.

The extra implementation cost over the naive approach is minimal — only `WorkflowRunner` differs. The data model, UI, loader, and Celery integration are identical either way.

## Proposed Solution

### 1. Workflow definition format

Workflows live in `workspace/workflows/`. Each workflow is a single YAML file:

```
workspace/workflows/
  weekly-news-digest.yml
  disk-monitor.yml
  competitor-watch.yml
```

**Format:**

```yaml
name: weekly-news-digest
description: Search for AI news every Monday and post a digest
agent: default                    # "default" or agent name; falls back to default agent
enabled: true
delivery: announce                # announce | silent | telegram

trigger:
  cron: "0 9 * * 1"              # 9am every Monday (5-field cron)
  timezone: Asia/Taipei           # optional, defaults to UTC

steps:
  - name: search
    prompt: >
      Search for the latest AI and LLM news from the past 7 days.
      Return a structured list of the top 5 stories with title, source, and one-sentence summary.

  - name: format-and-post
    prompt: >
      Format the news digest from the previous step into a clean, readable message
      and post it to Telegram using the send_telegram tool.
```

**Trigger types:**

```yaml
# Recurring cron
trigger:
  cron: "0 9 * * 1"
  timezone: Asia/Taipei

# Fixed interval
trigger:
  interval_minutes: 60

# One-shot (runs once, then sets enabled: false)
trigger:
  at: "2026-04-01T09:00:00+08:00"

# Webhook — out of scope for this spec
trigger:
  webhook: true
```

**Delivery modes:**

```yaml
delivery: announce   # post final step output to the conversation that created the workflow (default)
delivery: silent     # save output to AgentRun only, no notification
delivery: telegram   # post final step output to Telegram regardless of conversation
```

### 2. Data model

**`Workflow` model** (new):

```python
class Workflow(TimeStampedModel):
    id = UUIDField(primary_key=True, default=uuid4)
    name = CharField(max_length=100, unique=True)
    description = TextField(blank=True)
    agent = ForeignKey(Agent, null=True, blank=True, on_delete=SET_NULL)
    conversation = ForeignKey(Conversation, null=True, blank=True, on_delete=SET_NULL)
    enabled = BooleanField(default=True)
    definition = JSONField()             # parsed YAML stored as JSON
    filename = CharField(max_length=255) # relative path: workflows/<name>.yml
    delivery = CharField(max_length=20, default="announce")
    last_run_at = DateTimeField(null=True, blank=True)
    next_run_at = DateTimeField(null=True, blank=True)
    celery_beat_id = IntegerField(null=True, blank=True)  # PeriodicTask pk
```

**`AgentRun` additions:**
```python
workflow = ForeignKey(Workflow, null=True, blank=True, on_delete=SET_NULL, related_name="runs")
workflow_step = IntegerField(null=True, blank=True)   # 0-indexed step number
workflow_step_name = CharField(max_length=100, blank=True)
```

**Relationships:**
- `Workflow` → `Agent` (FK, nullable — falls back to default agent)
- `Workflow` → `Conversation` (FK, nullable — for `announce` delivery)
- `AgentRun` → `Workflow` (FK, nullable)

### 3. Workflow loader

`agent/workflows/loader.py` — `WorkflowLoader`:

1. Scan `workspace/workflows/*.yml`
2. Parse and validate YAML schema (required: `name`, `trigger`, `steps`)
3. Create or update `Workflow` model records
4. Register/update `django_celery_beat` `PeriodicTask`:
   - cron trigger → `CrontabSchedule` + `PeriodicTask`
   - interval trigger → `IntervalSchedule` + `PeriodicTask`
   - one-shot trigger → `ClockedSchedule` + `PeriodicTask` (one-off=True)
5. Delete stale `PeriodicTask` entries for removed or disabled workflows

Called from:
- `AgentConfig.ready()` on Django/Celery startup
- `WorkflowReloadView` (POST `/agent/workflows/reload/`) for manual refresh after file changes

### 4. Workflow runner — step isolation

`agent/workflows/runner.py` — `WorkflowRunner`:

```python
class WorkflowRunner:
    def run(self, workflow: Workflow) -> list[AgentRun]:
        steps = workflow.definition.get("steps", [])
        agent = workflow.agent or Agent.objects.filter(is_default=True, is_active=True).first()
        previous_output = None
        runs = []

        for i, step in enumerate(steps):
            prompt = step["prompt"]
            if previous_output and i > 0:
                prompt = (
                    f"Previous step output:\n{previous_output}\n\n"
                    f"Current step — {step['name']}:\n{prompt}"
                )

            run = AgentRun.objects.create(
                agent=agent,
                conversation=workflow.conversation,
                trigger_source=AgentRun.TriggerSource.WORKFLOW,
                status=AgentRun.Status.PENDING,
                input=prompt,
                workflow=workflow,
                workflow_step=i,
                workflow_step_name=step.get("name", f"step-{i+1}"),
            )
            AgentRunner().run_sync(run)
            run.refresh_from_db()
            previous_output = run.output

            if run.status == AgentRun.Status.FAILED:
                break  # abort remaining steps on failure

        workflow.last_run_at = timezone.now()
        workflow.save(update_fields=["last_run_at"])

        # Deliver final output
        if previous_output:
            _deliver(workflow, previous_output)

        # One-shot: disable after running
        if workflow.definition.get("trigger", {}).get("at"):
            workflow.enabled = False
            workflow.save(update_fields=["enabled"])

        return runs
```

**Step input format** (what each step's `AgentRun.input` looks like):

- Step 1: the raw prompt from the YAML
- Step N (N > 1):
  ```
  Previous step output:
  <output from step N-1>

  Current step — <step name>:
  <prompt from YAML>
  ```

Each step gets a clean system prompt (no accumulated history), but has the previous step's output as context in its input message.

### 5. Celery task

```python
@shared_task
def execute_workflow(workflow_id: str) -> None:
    workflow = Workflow.objects.get(pk=workflow_id)
    if not workflow.enabled:
        return
    WorkflowRunner().run(workflow)
```

Registered as a `PeriodicTask` by the loader with `kwargs={"workflow_id": str(workflow.id)}`.

### 6. Delivery

```python
def _deliver(workflow: Workflow, output: str) -> None:
    if workflow.delivery == "silent":
        return
    if workflow.delivery == "telegram" or (workflow.delivery == "announce" and not workflow.conversation_id):
        # send via Telegram interface
        ...
    if workflow.delivery == "announce" and workflow.conversation_id:
        Message.objects.create(
            conversation=workflow.conversation,
            role=Message.Role.ASSISTANT,
            content=output,
        )
```

### 7. Chat integration

The agent creates workflows from conversation using `file_write` + API reload:

```
User: Every Monday at 9am, search for AI news and send me a summary.

Agent: [calls file_write → workspace/workflows/weekly-ai-news.yml]
       [calls api_post → /agent/workflows/reload/]
Done — "weekly-ai-news" workflow created. First run: Monday 9am.
```

A `workflow-management` skill (`workspace/skills/workflow-management/SKILL.md`) teaches the agent:
- The full YAML schema with all valid fields
- Valid trigger formats (cron syntax, interval, one-shot)
- How to write to `workspace/workflows/`
- How to call `api_post` to `/agent/workflows/reload/` after writing
- How to set `conversation` to the current conversation ID for `announce` delivery

### 8. Agent UI

**Workflow list** (`/agent/workflows/`):
- Table: name, description, schedule, agent, delivery, enabled toggle, last run, next run, step count
- "Reload" button (POST to `/agent/workflows/reload/`)
- "New workflow" button (opens YAML editor with template)

**Workflow detail** (`/agent/workflows/<id>/`):
- YAML editor (editable, saves back to filesystem + reloads)
- Step list with status from last run
- Run history (filtered `AgentRun` list showing all steps)
- Enable/disable toggle
- "Run now" button

**Run list** — add Workflow column; step runs grouped under their workflow execution.

**Run detail** — show workflow name, step name, step number for workflow-triggered runs.

### 9. Retirement of HEARTBEAT.md

- Remove `heartbeat_task` Celery Beat schedule
- Keep `HeartbeatLog` model for historical audit (stop writing new records)
- Remove `HEARTBEAT.md` from `ALLOWED_WORKSPACE_FILES`
- Show deprecation notice in workspace file list if `HEARTBEAT.md` still exists
- Migration guide: convert checklist items to individual workflow YAML files

## Out of Scope

- Webhook triggers
- Per-step agent assignment (all steps use the same agent)
- Conditional branching between steps (`if step 1 returned X, skip step 2`)
- Workflow versioning / rollback
- Workflow marketplace / sharing
- Parallel step execution

## Acceptance Criteria

- [ ] Workflow YAML files in `workspace/workflows/` auto-load on startup
- [ ] Each workflow registers a `PeriodicTask` in `django_celery_beat`
- [ ] Multiple workflows can have different cron schedules and timezones
- [ ] Each step runs as a separate `AgentRun` with previous step output passed as context
- [ ] If a step fails, remaining steps are skipped and the workflow is marked failed
- [ ] `AgentRun` records include `workflow`, `workflow_step`, `workflow_step_name`
- [ ] Workflow list and detail views exist in the agent UI
- [ ] "Run now" triggers immediate execution of all steps
- [ ] Delivery modes `announce`, `silent`, `telegram` all work correctly
- [ ] Agent can create workflows from chat via `file_write` + reload
- [ ] `workflow-management` skill exists with full YAML schema reference
- [ ] One-shot workflows disable themselves after running
- [ ] `HEARTBEAT.md` mechanism is retired; deprecation notice shown if file exists

## Open Questions

- **Step failure handling**: Should a failed step abort the workflow (proposed) or continue to the next step with an error note in context?
- **Delivery conversation**: When the agent creates a workflow from chat, it should store the current `conversation_id` in the workflow for `announce` delivery. The `workflow-management` skill needs to instruct the agent to include this. How does the agent know its current conversation ID? **Proposed: inject `current_conversation_id` into the system prompt when a conversation is active.**
- **HEARTBEAT.md migration**: Provide a one-click "convert to workflows" tool in the UI, or leave manual migration? **Proposed: manual for now, document the mapping.**

## Implementation Notes

Implemented 2026-03-21.

**Files created:**
- `agent/workflows/__init__.py`
- `agent/workflows/loader.py` — `WorkflowLoader.load_all()`, scans `workspace/workflows/*.yml`
- `agent/workflows/runner.py` — `WorkflowRunner.run()`, step isolation
- `agent/workspace/skills/workflow-management/SKILL.md`
- `agent/workspace/workflows/` — directory for workflow YAML files
- `agent/templates/agent/workflow_list.html`
- `agent/templates/agent/workflow_detail.html`
- `agent/templates/agent/workflow_create.html`
- `agent/templates/agent/_workflow_toggle.html`
- `agent/migrations/0007_add_workflow_model.py`

**Files modified:**
- `agent/models.py` — added `Workflow` model; added `workflow`, `workflow_step`, `workflow_step_name` to `AgentRun`; added `WORKFLOW` to `TriggerSource`
- `agent/tasks.py` — added `execute_workflow` task
- `agent/apps.py` — calls `WorkflowLoader().load_all()` on startup
- `agent/views.py` — added workflow views, updated `ALLOWED_WORKSPACE_FILES`, added `heartbeat_deprecated` context
- `agent/urls.py` — added workflow URL patterns
- `agent/templates/agent/base_agent.html` — added Workflows nav link
- `agent/templates/agent/run_list.html` — added Workflow column
- `agent/templates/agent/_run_status.html` — added workflow/step info
- `agent/templates/agent/workspace.html` — added HEARTBEAT.md deprecation notice

**Deviations from spec:**
- `current_conversation_id` injection into system prompt (open question) — deferred; the workflow-management skill instructs the agent to include it manually from context.
- Telegram delivery `send_telegram` import path is a placeholder; actual interface import may differ.

# 002 — Chat UI

**Status:** Draft
**Created:** 2026-03-19

---

## Goal

Build a ChatGPT-style web interface for the `chat` app: a persistent sidebar showing conversation history and a "New Chat" button, with a model selector in the conversation header so users can switch LLMs per conversation.

---

## Background

Spec 001 defines the `Conversation` and `Message` models and the API endpoints. This spec covers only the visual design and HTMX interaction patterns for the web interface. Authentication and multi-tenancy are out of scope (spec 001 deferred them); for now all conversations are unscoped.

---

## Layout

```
┌──────────────────────────────────────────────────────────┐
│  Sidebar (260px fixed)     │  Main content area          │
│                            │                              │
│  [+ New Chat]              │  ┌──────────────────────┐   │
│  ─────────────────         │  │  Conversation header  │   │
│  Today                     │  │  model selector ▾     │   │
│    › My first chat         │  └──────────────────────┘   │
│    › Draft a cover letter  │                              │
│  Yesterday                 │  message bubbles…            │
│    › Summarise article     │                              │
│    › Python help           │  ─────────────────────────  │
│  ─────────────────         │  [  Type a message…    ] ▶  │
│                            │                              │
└──────────────────────────────────────────────────────────┘
```

### Sidebar

- Fixed width (~260 px), full viewport height, dark background (`#202123` or equivalent).
- **New Chat** button at the top — full-width, prominent. Clicking it `POST /chat/conversations/` and redirects to the new conversation.
- **Conversation list** grouped by recency: *Today*, *Yesterday*, *Previous 7 days*, *Older*. Each item is a single line (truncated title). Active conversation is highlighted.
- Clicking a conversation navigates to `/chat/conversations/<uuid>/` — full page navigation (no HTMX needed here).
- Sidebar is always visible on desktop; collapses to a hamburger toggle on mobile.

### Conversation header

- Slim bar at the top of the main area.
- Left: conversation title (editable inline, optional for now — clicking shows an `<input>`).
- Right: **model selector** — a `<select>` or custom dropdown listing available models. Changing it updates `Conversation.system_prompt` or a new `model` field (see Data Model Changes below). Sends `PATCH /chat/conversations/<uuid>/` via HTMX `hx-trigger="change"`.

### Message area

- Scrollable. Messages rendered in chronological order.
- **User messages**: right-aligned bubble, light background.
- **Assistant messages**: left-aligned, no bubble — flush with left edge like ChatGPT. Markdown rendered client-side (marked.js or equivalent, loaded from CDN).
- While the assistant is replying: a three-dot animated typing indicator appears as the last message.
- Messages stream in via SSE (`/chat/conversations/<uuid>/messages/<uuid>/stream/`). HTMX `hx-ext="sse"` appends tokens to the last assistant message bubble as they arrive.

### Input bar

- Pinned to the bottom of the main area.
- `<textarea>` that auto-expands up to ~5 rows. Submits on Enter (Shift+Enter for newline). Has a send `<button>` on the right.
- While a reply is in-flight: send button becomes a stop/cancel button; input is disabled.
- After submission: user message appears immediately (optimistic UI via HTMX `hx-swap="beforeend"` into the message list), then SSE takes over for the assistant reply.

---

## Data Model Changes

### Add `model` field to `Conversation`

```python
# chat/models.py
model = models.CharField(
    max_length=100,
    blank=True,
    default="",
    help_text="litellm model string, e.g. 'openai/gpt-4o'. Empty = use site default.",
)
```

`ChatService` will use `conversation.model or settings.LITELLM_DEFAULT_MODEL` when calling litellm.

### Available models list

Define the selectable models in settings so the template can iterate over them:

```python
# config/settings/base.py
AVAILABLE_MODELS = [
    ("openai/gpt-4o-mini",        "GPT-4o mini"),
    ("openai/gpt-4o",             "GPT-4o"),
    ("anthropic/claude-sonnet-4-6",  "Claude Sonnet 4.6"),
    ("anthropic/claude-opus-4-6",    "Claude Opus 4.6"),
]
```

Pass this to templates via a context processor or directly in views.

---

## URL Changes

Add one endpoint to support the inline model update:

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `PATCH` | `/chat/conversations/<uuid>/` | Update conversation (title, model) | `204 No Content` or HTMX OOB swap |

---

## Templates

```
chat/templates/chat/
├── base_chat.html           # extends base.html; adds sidebar + layout shell
├── conversation_list.html   # sidebar partial (for HTMX OOB refresh after new chat)
├── conversation.html        # full conversation page
├── _message.html            # single message bubble partial (streamed / HTMX swap)
├── _message_list.html       # full message list (initial page load)
└── _typing_indicator.html   # three-dot animation while reply is pending
```

`base_chat.html` structure:

```html
<div class="chat-layout">
  <aside class="sidebar">
    {% include "chat/_sidebar.html" %}
  </aside>
  <main class="chat-main">
    {% block chat_content %}{% endblock %}
  </main>
</div>
```

---

## Styling

- Use Tailwind CSS via CDN Play (no build step).
- Colour scheme: dark throughout — sidebar (`#202123`) and main area (`#343541`).
- User bubbles: dark gray (`#40414f`). Assistant messages: no bubble, white text on dark background.
- Input bar: dark background with slightly lighter textarea (`#40414f`), white text.
- No external UI component library — plain HTML + Tailwind utilities.
- Responsive: sidebar collapses at `md` breakpoint using Alpine.js toggle.

---

## HTMX Interaction Map

| User action | HTMX trigger | Target | Swap |
|-------------|-------------|--------|------|
| Click "New Chat" | `hx-post="/chat/conversations/"` | — | redirect (HX-Redirect header) |
| Send message | `hx-post=".../messages/"` | `#message-list` | `beforeend` |
| Model selector change | `hx-patch=".../conversations/<uuid>/"` | `#model-selector` | `none` (silent) |
| SSE token stream | `hx-ext="sse"` | `#assistant-msg-<uuid>` | `beforeend` |
| Poll for pending reply | `hx-get=".../messages/<uuid>/stream/"` | `#assistant-msg-<uuid>` | `innerHTML` every 1s (fallback if SSE not used) |

---

## Out of Scope

- User authentication / per-user conversation scoping (spec 001 deferred)
- Conversation search or filtering
- File / image attachments
- Message editing or regeneration
- Dark / light mode toggle (dark sidebar, light main is fixed for now)
- Agent runs UI (covered by spec 001; separate template tree under `agent/`)

---

## Acceptance Criteria

- [ ] Sidebar renders all conversations grouped by recency; active conversation is highlighted
- [ ] "New Chat" creates a conversation and navigates to it without a full page reload on the sidebar
- [ ] Model selector reflects the conversation's current model; changing it persists immediately
- [ ] Sending a message appends the user bubble instantly and streams the assistant reply token-by-token via SSE
- [ ] Layout is responsive: sidebar collapses on screens narrower than 768 px
- [ ] Markdown in assistant replies is rendered (bold, code blocks, lists)

---

## Open Questions

| # | Question | Owner | Target |
|---|----------|-------|--------|
| 1 | Should `AVAILABLE_MODELS` be editable via Django admin or hard-coded in settings? | — | Before implementation |
| 2 | Should conversation titles be auto-generated (first user message truncated) or manually entered? | — | Before implementation |
| 3 | Is SSE streaming required for MVP or is polling acceptable initially? | — | Before implementation |

---

## Implementation Notes

_To be filled in during implementation. Record deviations from the proposed solution and why._

# 007 — Chart Rendering

## Goal

Enable the agent to generate charts (bar, line, pie, scatter, etc.) and display
them inline in the chat UI as part of its reply.

## Background

The agent can already write Python scripts and execute them via `shell`, but:
- The chat template renders plain text — images are not displayed
- Workspace files are not served over HTTP, so there is no URL to reference

Spec 006 (MCP) is the right long-term mechanism for adding new capabilities
including charting. Once MCP is implemented, a local stdio MCP server (a small
Python process using `matplotlib`) can expose a `generate_chart` tool without
any Django code changes.

This spec covers the prerequisite infrastructure that is needed regardless of
how charts are generated: markdown rendering in chat and a workspace file-serving
endpoint. The custom `chart` Django tool described below is a **temporary fallback**
for use before spec 006 is implemented — it should be retired once an MCP chart
server is available.

## Proposed Solution

### 1. New `chart` tool (`agent/tools/chart.py`)

Accepts structured chart parameters and generates a PNG using `matplotlib`.
Saves the file to `AGENT_WORKSPACE_DIR` and returns the public URL.

```python
class ChartTool(BaseTool):
    name = "chart"
    description = (
        "Generate a chart image (bar, line, pie, scatter) and return a URL "
        "to embed in your reply. Use this whenever visualising data would help "
        "the user understand the answer."
    )
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "chart_type": {
                "type": "string",
                "enum": ["bar", "line", "pie", "scatter"],
                "description": "The type of chart to generate."
            },
            "title": {"type": "string", "description": "Chart title."},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Category labels (x-axis for bar/line, slice names for pie)."
            },
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Data values corresponding to each label."
            },
            "x_label": {"type": "string", "description": "X-axis label (optional)."},
            "y_label": {"type": "string", "description": "Y-axis label (optional)."},
        },
        "required": ["chart_type", "labels", "values"],
    }
```

Implementation:
- Use `matplotlib` (add to `pyproject.toml` via `uv add matplotlib`)
- Save as `chart_{uuid}.png` in `AGENT_WORKSPACE_DIR`
- Return `{"url": "/agent/workspace/chart_{uuid}.png", "filename": "chart_{uuid}.png"}`
- Use a dark-compatible style (`dark_background` or similar)

### 2. Workspace file-serving endpoint (`agent/urls.py`)

Add a view that serves files from `AGENT_WORKSPACE_DIR` by filename:

```
GET /agent/workspace/<filename>
```

- Restrict to image extensions: `.png`, `.jpg`, `.svg`
- Reject path traversal (filename must not contain `/` or `..`)
- Return `FileResponse` with appropriate `Content-Type`

### 3. Markdown rendering in chat messages (`chat/templates/chat/_message.html`)

Use a JS markdown library to render message content instead of outputting raw text.
Recommended: **marked.js** (lightweight, no build step needed).

- Load via CDN in `base_chat.html`
- Render `message.content` through `marked.parse()` into the message div
- This enables: `**bold**`, `` `code` ``, and `![alt](url)` image embeds

The agent includes the chart in its reply as:
```
Here is the alphabet frequency chart:

![Alphabet frequency](url_from_chart_tool)
```

### 4. AGENTS.md update

Add a note instructing the agent to use the `chart` tool when visualising data
would improve the answer, and to embed the returned URL using markdown image syntax.

### 5. Tool auto-discovery (`agent/tools/__init__.py`)

Currently every new tool must be manually added to `_init_registry()`. Replace
with auto-discovery: scan `agent/tools/` for modules, find all `BaseTool`
subclasses, and register them automatically.

```python
def _init_registry() -> None:
    import importlib
    import inspect
    from pathlib import Path

    tools_dir = Path(__file__).parent
    for path in sorted(tools_dir.glob("*.py")):
        if path.stem in ("__init__", "base"):
            continue
        module = importlib.import_module(f"agent.tools.{path.stem}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseTool) and obj is not BaseTool:
                _register(obj())
```

After this change, adding a new tool only requires creating the file —
no registration step needed.

## Implementation Steps

1. `uv add matplotlib`
2. Replace `_init_registry()` in `agent/tools/__init__.py` with auto-discovery
3. Create `agent/tools/chart.py` with `ChartTool`
4. Add workspace file-serving view + URL in `agent/`
5. Add `marked.js` to `chat/templates/chat/base_chat.html`
6. Update `_message.html` to render markdown
7. Update `agent/workspace/AGENTS.md` with chart usage guidance

## Out of Scope

- Interactive charts (Plotly/Chart.js) — adds significant complexity; static PNG is sufficient for v1
- Multi-series charts — single series covers most use cases; can be extended later
- Chart editing or regeneration — the agent can be asked to redraw with new parameters

## Open Questions

- Should workspace image files be cleaned up automatically (e.g. after 24h)?
- Should the file-serving endpoint require authentication, or is it open (workspace is local-only anyway)?

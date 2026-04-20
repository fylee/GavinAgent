"""
agent.workspace — workspace directory helpers.

The workspace/ directory holds markdown files (AGENTS.md, SOUL.md, etc.)
used as context for agent runs. This module provides ensure_workspace(),
which creates any missing example/default files without overwriting existing ones.
"""

from pathlib import Path

from django.conf import settings


def ensure_workspace() -> None:
    """Create workspace directory and seed missing example files."""
    workspace_dir = Path(settings.AGENT_WORKSPACE_DIR)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    examples_dir = Path(__file__).parent

    for example_file in examples_dir.glob("*.example"):
        dest = workspace_dir / example_file.name
        if not dest.exists():
            dest.write_text(example_file.read_text(encoding="utf-8"), encoding="utf-8")

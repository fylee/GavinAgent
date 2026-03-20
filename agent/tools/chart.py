from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings

from .base import ApprovalPolicy, BaseTool, ToolResult


class ChartTool(BaseTool):
    name = "chart"
    description = (
        "Generate a chart image (bar, line, pie, scatter) and return a markdown image URL "
        "to embed in your reply. Use this whenever visualising data would help the user "
        "understand the answer. Embed the returned markdown in your response."
    )
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "chart_type": {
                "type": "string",
                "enum": ["bar", "line", "pie", "scatter"],
                "description": "The type of chart to generate.",
            },
            "title": {
                "type": "string",
                "description": "Chart title.",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Category labels (x-axis for bar/line, slice names for pie, x values for scatter).",
            },
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Data values corresponding to each label.",
            },
            "x_label": {
                "type": "string",
                "description": "X-axis label (optional).",
            },
            "y_label": {
                "type": "string",
                "description": "Y-axis label (optional).",
            },
        },
        "required": ["chart_type", "labels", "values"],
    }

    def execute(
        self,
        chart_type: str,
        labels: list,
        values: list,
        title: str = "",
        x_label: str = "",
        y_label: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        start = time.monotonic()
        try:
            import matplotlib
            matplotlib.use("Agg")  # non-interactive backend
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(8, 5))
            fig.patch.set_facecolor("#1e1e2e")
            ax.set_facecolor("#1e1e2e")
            ax.tick_params(colors="#cccccc")
            ax.xaxis.label.set_color("#cccccc")
            ax.yaxis.label.set_color("#cccccc")
            ax.title.set_color("#ffffff")
            for spine in ax.spines.values():
                spine.set_edgecolor("#444444")

            color = "#4f9cf9"

            if chart_type == "bar":
                ax.bar(labels, values, color=color)
            elif chart_type == "line":
                ax.plot(labels, values, color=color, marker="o")
            elif chart_type == "pie":
                ax.pie(values, labels=labels, autopct="%1.1f%%",
                       textprops={"color": "#cccccc"})
                ax.set_facecolor("#1e1e2e")
            elif chart_type == "scatter":
                numeric_labels = list(range(len(labels)))
                ax.scatter(numeric_labels, values, color=color)
                ax.set_xticks(numeric_labels)
                ax.set_xticklabels(labels, rotation=45, ha="right")

            if title:
                ax.set_title(title, pad=12)
            if x_label:
                ax.set_xlabel(x_label)
            if y_label:
                ax.set_ylabel(y_label)

            plt.tight_layout()

            filename = f"chart_{uuid.uuid4().hex[:8]}.png"
            workspace = Path(settings.AGENT_WORKSPACE_DIR)
            workspace.mkdir(parents=True, exist_ok=True)
            filepath = workspace / filename
            fig.savefig(filepath, dpi=120, facecolor=fig.get_facecolor())
            plt.close(fig)

            url = f"/agent/workspace-file/{filename}"
            markdown = f"![{title or chart_type} chart]({url})"
            return ToolResult(
                output={"url": url, "markdown": markdown, "filename": filename},
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return ToolResult(
                output=None,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

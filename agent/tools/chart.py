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
                "enum": ["bar", "barh", "line", "pie", "scatter"],
                "description": "The type of chart to generate. Use 'barh' for horizontal bars (better when there are many labels).",
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
            import matplotlib.font_manager as fm

            # ── Input sanitisation ────────────────────────────────────────────
            # 1. Coerce labels to str, values to float
            labels = [str(lb) for lb in labels]
            try:
                values = [float(v) for v in values]
            except (TypeError, ValueError):
                values = [float(v) if str(v).replace(".", "").lstrip("-").isdigit() else 0.0
                          for v in values]

            # 2. Align lengths — truncate whichever list is longer
            n = min(len(labels), len(values))
            labels, values = labels[:n], values[:n]

            if n == 0:
                return ToolResult(output=None, error="No data to chart: labels and values are both empty.")

            # 3. If this looks like a character-frequency chart (all labels are
            #    single characters), silently drop any non-letter entries so
            #    spaces/punctuation don't crowd the axis or crash matplotlib.
            all_single_chars = all(len(lb) == 1 for lb in labels)
            if all_single_chars:
                pairs = [(lb, v) for lb, v in zip(labels, values) if lb.isalpha() and v > 0]
                if pairs:
                    labels, values = zip(*pairs)
                    labels, values = list(labels), list(values)

            # 4. Fallback chart_type if unknown
            valid_types = {"bar", "barh", "line", "pie", "scatter"}
            if chart_type not in valid_types:
                chart_type = "bar"

            n_labels = len(labels)

            # ── Font setup ────────────────────────────────────────────────────
            _CJK_CANDIDATES = [
                "PingFang SC", "PingFang TC", "Heiti TC", "STHeiti",
                "Arial Unicode MS", "Noto Sans CJK SC", "Noto Sans SC",
                "WenQuanYi Micro Hei", "Microsoft JhengHei",
            ]
            _available = {f.name for f in fm.fontManager.ttflist}
            _cjk_font = next((f for f in _CJK_CANDIDATES if f in _available), None)
            if _cjk_font:
                plt.rcParams["font.family"] = _cjk_font
            plt.rcParams["axes.unicode_minus"] = False

            # ── Figure sizing ─────────────────────────────────────────────────
            if chart_type == "barh":
                fig_width = 8
                fig_height = max(5, n_labels * 0.35)
            else:
                fig_width = max(8, n_labels * 0.5)
                fig_height = 5
            fig, ax = plt.subplots(figsize=(fig_width, fig_height))
            fig.patch.set_facecolor("#1e1e2e")
            ax.set_facecolor("#1e1e2e")
            ax.tick_params(colors="#cccccc")
            ax.xaxis.label.set_color("#cccccc")
            ax.yaxis.label.set_color("#cccccc")
            ax.title.set_color("#ffffff")
            for spine in ax.spines.values():
                spine.set_edgecolor("#444444")

            color = "#4f9cf9"

            # ── Plot ──────────────────────────────────────────────────────────
            if chart_type == "bar":
                ax.bar(labels, values, color=color)
                ax.set_xticks(range(n_labels))
                ax.set_xticklabels(labels, rotation=45, ha="right",
                                   fontsize=max(7, 10 - n_labels // 5))
            elif chart_type == "barh":
                ax.barh(labels, values, color=color)
                ax.invert_yaxis()  # top label first
                ax.tick_params(axis="y", labelsize=max(7, 10 - n_labels // 8))
            elif chart_type == "line":
                ax.plot(labels, values, color=color, marker="o")
                ax.set_xticks(range(n_labels))
                ax.set_xticklabels(labels, rotation=45, ha="right",
                                   fontsize=max(7, 10 - n_labels // 5))
            elif chart_type == "pie":
                ax.pie(values, labels=labels, autopct="%1.1f%%",
                       textprops={"color": "#cccccc"})
                ax.set_facecolor("#1e1e2e")
            elif chart_type == "scatter":
                numeric_x = list(range(n_labels))
                ax.scatter(numeric_x, values, color=color)
                ax.set_xticks(numeric_x)
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

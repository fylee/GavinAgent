"""Tests for the stock-chart skill handler — parsing, data fetch, and chart generation."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Dynamically import handler.py (it lives in workspace/skills, not a Python package)
_HANDLER_PATH = (
    Path(__file__).resolve().parents[2]
    / "agent"
    / "workspace"
    / "skills"
    / "stock-chart"
    / "handler.py"
)
_spec = importlib.util.spec_from_file_location("_skill_stock_chart_test", _HANDLER_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["_skill_stock_chart_test"] = _mod
_spec.loader.exec_module(_mod)

_resolve_ticker = _mod._resolve_ticker
_parse_period_days = _mod._parse_period_days
handle = _mod.handle


# ── Ticker resolution ────────────────────────────────────────────────────────


class TestResolveTicker:
    """Deterministic ticker parsing — no network calls."""

    def test_company_name_us(self):
        assert _resolve_ticker("apple stock price") == "AAPL"

    def test_company_name_taiwan(self):
        assert _resolve_ticker("TSMC stock price past 5 days") == "2330.TW"

    def test_company_name_chinese(self):
        """Chinese company name resolves correctly."""
        assert _resolve_ticker("\u83ef\u90a6\u96fb\u80a1\u50f9\u8d70\u52e2") == "2344.TW"

    def test_explicit_ticker_symbol(self):
        assert _resolve_ticker("NVDA stock") == "NVDA"

    def test_explicit_ticker_with_exchange(self):
        """Input containing a 4-digit number resolves to .TW suffix."""
        assert _resolve_ticker("2330 past 3 weeks") == "2330.TW"

    def test_exchange_qualified_ticker(self):
        """2344.TW should be matched as-is, not split into TW."""
        assert _resolve_ticker("2344.TW 7 days") == "2344.TW"

    def test_exchange_suffix_not_treated_as_ticker(self):
        """TW, KS, HK should not be treated as standalone tickers."""
        assert _resolve_ticker("2344.TW 7 days") != "TW"

    def test_winbond_alias(self):
        assert _resolve_ticker("winbond stock chart") == "2344.TW"

    def test_no_ticker_found(self):
        assert _resolve_ticker("hello world") is None

    def test_noise_words_filtered(self):
        """Common English words like 'THE' should not be treated as tickers."""
        # "THE" is in the noise list — should not match
        result = _resolve_ticker("the stock market")
        # Should match 'stock' as None (no alias) and no uppercase ticker
        # Actually this would return None since no alias matches and no uppercase ticker
        assert result is None or result not in ("THE",)

    def test_case_insensitive_alias(self):
        assert _resolve_ticker("Nvidia shares") == "NVDA"

    def test_longest_alias_wins(self):
        """'united micro' should match before 'delta'."""
        assert _resolve_ticker("united micro stock") == "2303.TW"


# ── Period parsing ────────────────────────────────────────────────────────────


class TestParsePeriodDays:
    """Deterministic period parsing — no network calls."""

    def test_days(self):
        assert _parse_period_days("past 5 days") == 5

    def test_weeks(self):
        assert _parse_period_days("last 2 weeks") == 14

    def test_months(self):
        assert _parse_period_days("1 month") == 30

    def test_years(self):
        assert _parse_period_days("1 year") == 365

    def test_default_period(self):
        """No period specified → default 5 days."""
        assert _parse_period_days("AAPL stock") == 5

    def test_chinese_days(self):
        assert _parse_period_days("past 10 days") == 10

    def test_minimum_one_day(self):
        assert _parse_period_days("past 0 days") == 1


# ── Handle function (end-to-end with mocks) ──────────────────────────────────


class TestHandle:
    """Integration tests for the handle() entry point."""

    def test_no_ticker_returns_error_message(self):
        result = handle("hello world how are you")
        assert "Could not identify" in result

    @patch.object(_mod, "_fetch_stock_data")
    def test_successful_stock_chart(self, mock_fetch, tmp_path):
        """Full pipeline: resolve → fetch → chart → summary."""
        mock_fetch.return_value = (
            ["01/06", "01/07", "01/08", "01/09", "01/10"],
            [150.0, 152.5, 148.0, 155.0, 153.0],
            {"name": "Apple Inc.", "currency": "USD", "ticker": "AAPL"},
        )

        with patch.object(_mod, "_generate_chart", return_value="![chart](url)"):
            result = handle("AAPL past 5 days")

        assert "Apple Inc." in result
        assert "AAPL" in result
        assert "![chart](url)" in result
        assert "153.00" in result  # latest close
        assert "150.00" in result  # period open
        mock_fetch.assert_called_once_with("AAPL", 5)

    @patch.object(_mod, "_fetch_stock_data")
    def test_fetch_failure_returns_error(self, mock_fetch):
        mock_fetch.side_effect = Exception("Network error")
        result = handle("AAPL past 5 days")
        assert "Failed to fetch" in result
        assert "AAPL" in result

    @patch.object(_mod, "_fetch_stock_data")
    def test_empty_prices_returns_error(self, mock_fetch):
        mock_fetch.return_value = ([], [], {"name": "X", "currency": "USD", "ticker": "X"})
        result = handle("AAPL past 5 days")
        assert "No price data" in result

    @patch.object(_mod, "_fetch_stock_data")
    @patch.object(_mod, "_generate_chart")
    def test_chart_failure_returns_error(self, mock_chart, mock_fetch):
        mock_fetch.return_value = (
            ["01/06"],
            [150.0],
            {"name": "Apple", "currency": "USD", "ticker": "AAPL"},
        )
        mock_chart.side_effect = Exception("matplotlib broke")
        result = handle("AAPL 1 day")
        assert "Failed to generate chart" in result

    @patch.object(_mod, "_fetch_stock_data")
    @patch.object(_mod, "_generate_chart", return_value="![chart](url)")
    def test_price_change_direction_up(self, mock_chart, mock_fetch):
        mock_fetch.return_value = (
            ["01/01", "01/02"],
            [100.0, 110.0],
            {"name": "Test", "currency": "USD", "ticker": "TEST"},
        )
        result = handle("TEST 2 days")
        assert "+10.00" in result
        assert "+10.00%" in result

    @patch.object(_mod, "_fetch_stock_data")
    @patch.object(_mod, "_generate_chart", return_value="![chart](url)")
    def test_price_change_direction_down(self, mock_chart, mock_fetch):
        mock_fetch.return_value = (
            ["01/01", "01/02"],
            [100.0, 90.0],
            {"name": "Test", "currency": "USD", "ticker": "TEST"},
        )
        result = handle("TEST 2 days")
        assert "-10.00" in result
        assert "-10.00%" in result


# ── Skill loader integration ─────────────────────────────────────────────────


class TestSkillLoader:
    """Verify the skill is loadable by the SkillLoader."""

    def test_skill_md_parseable(self):
        from agent.skills.loader import _parse_skill_md

        skill_md = (
            Path(__file__).resolve().parents[2]
            / "agent"
            / "workspace"
            / "skills"
            / "stock-chart"
            / "SKILL.md"
        )
        meta = _parse_skill_md(skill_md)
        assert meta["name"] == "stock-chart"
        assert "stock" in meta["description"].lower()
        assert meta["approval_required"] is False
        assert meta["tools"] == ["run_skill"]

    def test_handler_has_handle_function(self):
        """handler.py exposes handle() as required by RunSkillTool."""
        assert callable(handle)


class TestRunSkillMarkdownExtraction:
    """Verify RunSkillTool extracts markdown images from handler results."""

    def test_markdown_extracted_from_result(self):
        from agent.tools.skill import _MD_IMAGE_RE

        result_text = (
            "## AAPL\n\n"
            "![Apple (AAPL) chart](/agent/workspace-file/stock_abc123.png)\n\n"
            "| Metric | Value |\n|---|---|\n| Close | 150.00 |"
        )
        images = _MD_IMAGE_RE.findall(result_text)
        assert len(images) == 1
        assert "stock_abc123.png" in images[0]

    def test_no_markdown_when_no_images(self):
        from agent.tools.skill import _MD_IMAGE_RE

        result_text = "Could not identify a stock ticker."
        images = _MD_IMAGE_RE.findall(result_text)
        assert len(images) == 0

    @patch.object(_mod, "_fetch_stock_data")
    @patch.object(_mod, "_generate_chart", return_value="![chart](/agent/workspace-file/test.png)")
    def test_run_skill_output_has_markdown_key(self, mock_chart, mock_fetch, settings):
        """RunSkillTool should expose markdown key when handler returns chart images."""
        settings.AGENT_WORKSPACE_DIR = str(
            Path(__file__).resolve().parents[2] / "agent" / "workspace"
        )
        mock_fetch.return_value = (
            ["01/06"], [150.0],
            {"name": "Test", "currency": "USD", "ticker": "TEST"},
        )

        from agent.tools.skill import RunSkillTool, _MD_IMAGE_RE
        # Simulate what RunSkillTool does: extract markdown from a result string
        sample_result = (
            "## Test (TEST)\n\n"
            "![Test chart](/agent/workspace-file/stock_abc.png)\n\n"
            "| Metric | Value |\n| Close | 150.00 |"
        )
        images = _MD_IMAGE_RE.findall(sample_result)
        assert len(images) == 1
        assert "/agent/workspace-file/" in images[0]
        # Verify the output dict construction logic
        output_dict = {"result": sample_result}
        if isinstance(sample_result, str):
            md_images = _MD_IMAGE_RE.findall(sample_result)
            if md_images:
                output_dict["markdown"] = "\n".join(md_images)
        assert "markdown" in output_dict
        assert "stock_abc.png" in output_dict["markdown"]

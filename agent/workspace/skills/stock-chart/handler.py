"""
Stock-chart skill handler — deterministic pipeline for stock price charts.

Uses yfinance for data and matplotlib for charting.
No LLM involvement — parses input, fetches data, draws chart, returns markdown.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf

# ── Common ticker aliases (company name → Yahoo Finance symbol) ───────────────
TICKER_ALIASES: dict[str, str] = {
    # US
    "apple": "AAPL",
    "aapl": "AAPL",
    "microsoft": "MSFT",
    "msft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "googl": "GOOGL",
    "amazon": "AMZN",
    "amzn": "AMZN",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "tsla": "TSLA",
    "nvidia": "NVDA",
    "nvda": "NVDA",
    "amd": "AMD",
    "intel": "INTC",
    "intc": "INTC",
    # Taiwan
    "tsmc": "2330.TW",
    "台積電": "2330.TW",
    "winbond": "2344.TW",
    "華邦電": "2344.TW",
    "華邦電子": "2344.TW",
    "foxconn": "2317.TW",
    "鴻海": "2317.TW",
    "mediatek": "2454.TW",
    "聯發科": "2454.TW",
    "asus": "2357.TW",
    "華碩": "2357.TW",
    "acer": "2353.TW",
    "宏碁": "2353.TW",
    "hon hai": "2317.TW",
    "united micro": "2303.TW",
    "聯電": "2303.TW",
    "delta": "2308.TW",
    "台達電": "2308.TW",
    # Japan
    "sony": "6758.T",
    "toyota": "7203.T",
    "nintendo": "7974.T",
    # Korea
    "samsung": "005930.KS",
    # HK
    "tencent": "0700.HK",
    "alibaba": "9988.HK",
}

# ── Period parsing ────────────────────────────────────────────────────────────

_PERIOD_PATTERN = re.compile(
    r"(?:past|last|recent|近|前)?\s*(\d+)\s*(day|days|天|日|week|weeks|週|周|month|months|個月|月|year|years|年)",
    re.IGNORECASE,
)

_PERIOD_SHORTCUTS = {
    "1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365, "ytd": 365,
}


def _parse_period_days(text: str) -> int:
    """Extract number of calendar days from natural language. Default 5."""
    m = _PERIOD_PATTERN.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit in ("day", "days", "天", "日"):
            return max(1, n)
        if unit in ("week", "weeks", "週", "周"):
            return n * 7
        if unit in ("month", "months", "個月", "月"):
            return n * 30
        if unit in ("year", "years", "年"):
            return n * 365
    # Check shortcuts
    lower = text.lower()
    for shortcut, days in _PERIOD_SHORTCUTS.items():
        if shortcut in lower:
            return days
    return 5  # default: 5 days


def _resolve_ticker(text: str) -> str | None:
    """Try to resolve a ticker symbol from the input text."""
    lower = text.lower().strip()

    # 1. Direct alias match (longest match first)
    for alias in sorted(TICKER_ALIASES.keys(), key=len, reverse=True):
        if alias in lower:
            return TICKER_ALIASES[alias]

    # 2. Explicit exchange-qualified tickers: 2330.TW, 005930.KS, 0700.HK, 6758.T
    exchange_match = re.search(r"\b(\d{4,6}\.[A-Z]{1,2})\b", text)
    if exchange_match:
        return exchange_match.group(1)

    # 3. Look for Taiwan-style numeric tickers: 2330, 2344
    tw_match = re.search(r"\b(\d{4})\b", text)
    if tw_match:
        return f"{tw_match.group(1)}.TW"

    # 4. Look for US-style uppercase ticker symbols: AAPL, NVDA
    ticker_match = re.search(r"\b([A-Z]{2,5})\b", text)
    if ticker_match:
        candidate = ticker_match.group(1)
        # Filter out common English words and exchange suffixes
        noise = {
            "THE", "AND", "FOR", "NOT", "ARE", "BUT", "HAS", "HAD", "WAS",
            "HIS", "HER", "ITS", "YOU", "ALL", "CAN", "HER", "ONE", "OUR",
            "OUT", "DAY", "GET", "HIM", "HOW", "MAN", "NEW", "NOW", "OLD",
            "SEE", "WAY", "MAY", "SAY", "SHE", "TWO", "USE", "BOY",
            "DID", "OWN", "LET", "PUT", "TOO", "ANY",
            # Exchange suffixes that appear after dots (e.g. .TW, .KS, .HK)
            "TW", "KS", "HK", "SS", "SZ",
        }
        if candidate not in noise:
            return candidate

    return None


def _fetch_stock_data(
    ticker: str, days: int
) -> tuple[list[str], list[float], dict[str, Any]]:
    """
    Fetch historical close prices.
    Returns (dates, prices, metadata).
    """
    # Add buffer days for weekends/holidays
    buffer_days = days + (days // 5 + 1) * 3
    end = datetime.now()
    start = end - timedelta(days=buffer_days)

    stock = yf.Ticker(ticker)
    hist = stock.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))

    if hist.empty:
        raise ValueError(f"No data found for ticker '{ticker}'. Please check the symbol.")

    # Take the last N trading days
    hist = hist.tail(days)

    dates = [d.strftime("%m/%d") for d in hist.index]
    prices = [round(float(p), 2) for p in hist["Close"]]

    info = stock.info if hasattr(stock, "info") else {}
    metadata = {
        "name": info.get("shortName") or info.get("longName") or ticker,
        "currency": info.get("currency", "USD"),
        "ticker": ticker,
    }

    return dates, prices, metadata


def _generate_chart(
    dates: list[str],
    prices: list[float],
    title: str,
    currency: str,
) -> str:
    """Generate a line chart, save to workspace, return markdown image link."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm

    # ── Font setup (CJK support) ─────────────────────────────────────────
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

    # ── Dark theme chart ──────────────────────────────────────────────────
    fig_width = max(8, len(dates) * 0.6)
    fig, ax = plt.subplots(figsize=(fig_width, 5))
    fig.patch.set_facecolor("#1e1e2e")
    ax.set_facecolor("#1e1e2e")
    ax.tick_params(colors="#cccccc")
    ax.xaxis.label.set_color("#cccccc")
    ax.yaxis.label.set_color("#cccccc")
    ax.title.set_color("#ffffff")
    for spine in ax.spines.values():
        spine.set_edgecolor("#444444")

    # Plot with markers
    color = "#4f9cf9"
    ax.plot(range(len(dates)), prices, color=color, marker="o", linewidth=2, markersize=5)

    # Fill area under the line
    ax.fill_between(range(len(dates)), prices, alpha=0.1, color=color)

    # Annotate min/max
    if prices:
        max_idx = prices.index(max(prices))
        min_idx = prices.index(min(prices))
        ax.annotate(
            f"{prices[max_idx]}",
            xy=(max_idx, prices[max_idx]),
            xytext=(0, 10), textcoords="offset points",
            ha="center", color="#4caf50", fontsize=9, fontweight="bold",
        )
        ax.annotate(
            f"{prices[min_idx]}",
            xy=(min_idx, prices[min_idx]),
            xytext=(0, -15), textcoords="offset points",
            ha="center", color="#f44336", fontsize=9, fontweight="bold",
        )

    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels(dates, rotation=45, ha="right", fontsize=9)
    ax.set_title(title, pad=12, fontsize=13)
    ax.set_xlabel("Date")
    ax.set_ylabel(f"Price ({currency})")
    ax.grid(True, alpha=0.2, color="#666666")

    plt.tight_layout()

    # Save to workspace
    from django.conf import settings as django_settings
    workspace = Path(django_settings.AGENT_WORKSPACE_DIR)
    workspace.mkdir(parents=True, exist_ok=True)
    filename = f"stock_{uuid.uuid4().hex[:8]}.png"
    filepath = workspace / filename
    fig.savefig(filepath, dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)

    url = f"/agent/workspace-file/{filename}"
    return f"![{title}]({url})"


def handle(input: str) -> str:
    """
    Main entry point — called by RunSkillTool.

    Accepts natural language like:
      - "TSMC stock price past 5 days"
      - "AAPL 1 month"
      - "2330.TW 10 days"
      - "華邦電股價走勢"
    """
    # 1. Resolve ticker
    ticker = _resolve_ticker(input)
    if not ticker:
        return (
            "Could not identify a stock ticker from your request. "
            "Please include a ticker symbol (e.g. AAPL, 2330.TW) or "
            "a company name (e.g. TSMC, Apple, 華邦電)."
        )

    # 2. Parse time period
    days = _parse_period_days(input)

    # 3. Fetch data
    try:
        dates, prices, metadata = _fetch_stock_data(ticker, days)
    except Exception as e:
        return f"Failed to fetch stock data for **{ticker}**: {e}"

    if not prices:
        return f"No price data available for **{ticker}**."

    company = metadata["name"]
    currency = metadata["currency"]

    # 4. Generate chart
    try:
        chart_md = _generate_chart(
            dates, prices,
            title=f"{company} ({ticker}) — Last {len(dates)} Trading Days",
            currency=currency,
        )
    except Exception as e:
        return f"Failed to generate chart: {e}"

    # 5. Build summary
    latest = prices[-1]
    earliest = prices[0]
    change = latest - earliest
    pct = (change / earliest * 100) if earliest else 0
    high = max(prices)
    low = min(prices)
    direction = "📈" if change >= 0 else "📉"

    summary = (
        f"## {company} ({ticker}) {direction}\n\n"
        f"{chart_md}\n\n"
        f"**Period:** {dates[0]} – {dates[-1]} ({len(dates)} trading days)\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Latest Close | {latest:,.2f} {currency} |\n"
        f"| Period Open | {earliest:,.2f} {currency} |\n"
        f"| Change | {change:+,.2f} ({pct:+.2f}%) |\n"
        f"| Period High | {high:,.2f} {currency} |\n"
        f"| Period Low | {low:,.2f} {currency} |\n"
    )

    return summary

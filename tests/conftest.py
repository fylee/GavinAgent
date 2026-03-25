"""Root conftest — shared fixtures for all tests."""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def workspace_dir(tmp_path, settings):
    """Copy fixture workspace to a temp dir and point settings at it."""
    import shutil

    src = FIXTURES_DIR / "workspace"
    dst = tmp_path / "workspace"
    shutil.copytree(src, dst)
    settings.AGENT_WORKSPACE_DIR = str(dst)
    return dst


@pytest.fixture()
def mock_embed():
    """Patch core.memory.embed_text to return a deterministic 1536-dim vector."""
    fake_vector = [0.01] * 1536

    with patch("core.memory.embed_text", return_value=fake_vector) as m:
        yield m


@pytest.fixture()
def mock_llm():
    """Patch core.llm.get_completion to return a canned assistant response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Test response"
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage = MagicMock(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )

    with patch("core.llm.get_completion", return_value=mock_response) as m:
        m.mock_response = mock_response
        yield m

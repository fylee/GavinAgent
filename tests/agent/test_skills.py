"""P0 tests for agent.skills.loader — pure logic, no DB."""
from __future__ import annotations

from pathlib import Path

from agent.skills.loader import _parse_skill_md


class TestParseSkillMd:
    def test_valid_frontmatter(self, tmp_path):
        """Extracts YAML frontmatter and body correctly."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\n"
            "name: weather\n"
            "description: Get weather info\n"
            "trigger: weather\n"
            "tools:\n"
            "  - web_read\n"
            "  - api_get\n"
            "---\n"
            "\n"
            "## Instructions\n"
            "\n"
            "Look up the weather.\n",
            encoding="utf-8",
        )
        meta = _parse_skill_md(skill_md)
        assert meta["name"] == "weather"
        assert meta["description"] == "Get weather info"
        assert meta["tools"] == ["web_read", "api_get"]
        assert "Instructions" in meta["instructions"]
        assert "Look up the weather." in meta["instructions"]

    def test_no_frontmatter(self, tmp_path):
        """Returns empty dict when no --- markers."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("Just some text without frontmatter.\n", encoding="utf-8")
        assert _parse_skill_md(skill_md) == {}

    def test_empty_file(self, tmp_path):
        """Returns empty dict for empty file."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("", encoding="utf-8")
        assert _parse_skill_md(skill_md) == {}

    def test_single_dash_line(self, tmp_path):
        """Returns empty dict with only one --- (incomplete frontmatter)."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\n", encoding="utf-8")
        assert _parse_skill_md(skill_md) == {}

    def test_tools_list_extracted(self, tmp_path):
        """tools field is parsed as a Python list."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\n"
            "name: analyst\n"
            "tools:\n"
            "  - web_read\n"
            "  - web_search\n"
            "  - chart\n"
            "---\n"
            "Instructions here.\n",
            encoding="utf-8",
        )
        meta = _parse_skill_md(skill_md)
        assert meta["tools"] == ["web_read", "web_search", "chart"]

    def test_instructions_stripped(self, tmp_path):
        """Body text after --- is stripped of leading/trailing whitespace."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\nname: test\n---\n\n  Hello world  \n\n",
            encoding="utf-8",
        )
        meta = _parse_skill_md(skill_md)
        assert meta["instructions"] == "Hello world"

    def test_missing_name_returns_no_name(self, tmp_path):
        """Frontmatter without name key returns dict without name."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\ndescription: no name here\n---\nBody.\n",
            encoding="utf-8",
        )
        meta = _parse_skill_md(skill_md)
        assert "name" not in meta
        assert meta["description"] == "no name here"

    def test_empty_yaml_body(self, tmp_path):
        """Empty YAML between --- returns empty dict (yaml.safe_load returns None → {})."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\n---\nBody text.\n", encoding="utf-8")
        meta = _parse_skill_md(skill_md)
        # YAML is empty → meta is just {"instructions": "Body text."}
        assert meta.get("instructions") == "Body text."

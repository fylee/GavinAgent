"""Tests for Spec 026 — Skill Scale Optimization.

Covers:
  - Component 1: _get_category_from_path, skill_catalog_for_prompt (Tier 0 + Tier 1)
  - Component 2: skill_matches_platform, sync_claude_code platform filtering
  - Component 3: _append_supporting_files_hint (Tier 3 on-demand reference hints)
  - Component 4: get_disabled_skills, _parse_platform_disabled, collect_all_skills, sync disabled

Test style: pytest + tmp_path.  Pure function tests need no Django setup.
DB-dependent tests use @pytest.mark.django_db.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_skill(
    skills_dir: Path,
    name: str,
    frontmatter: dict | None = None,
    body: str = "## Instructions\n\nDo something.",
    subdirs: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Create a skill directory with SKILL.md and optional subdirectories.

    name may include a category prefix, e.g. "cim/cim-eda" — the intermediate
    directories are created automatically.
    """
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    leaf_name = Path(name).name
    meta = frontmatter or {"name": leaf_name, "description": f"{leaf_name} skill"}
    fm = yaml.dump(meta, allow_unicode=True, sort_keys=False).rstrip()
    (skill_dir / "SKILL.md").write_text(f"---\n{fm}\n---\n\n{body}", encoding="utf-8")

    if subdirs:
        for subdir_name, files in subdirs.items():
            subdir = skill_dir / subdir_name
            subdir.mkdir(parents=True, exist_ok=True)
            for fname, content in files.items():
                (subdir / fname).write_text(content, encoding="utf-8")

    return skill_dir


def _make_sync_cmd():
    from agent.management.commands.sync_claude_code import Command

    cmd = Command()
    cmd.stdout = StringIO()
    cmd.stderr = StringIO()
    cmd.style = MagicMock()
    cmd.style.SUCCESS = lambda s: s
    return cmd


# ══════════════════════════════════════════════════════════════════════════════
# Component 1 — Category directory structure
# ══════════════════════════════════════════════════════════════════════════════


class TestGetCategoryFromPath:
    def test_two_level_returns_category(self, tmp_path):
        """category/skill-name/SKILL.md → 'category'"""
        base = tmp_path / "skills"
        skill_md = base / "cim" / "cim-eda" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.touch()
        from agent.skills.discovery import _get_category_from_path
        assert _get_category_from_path(skill_md, base) == "cim"

    def test_one_level_returns_none(self, tmp_path):
        """skill-name/SKILL.md (flat layout) → None"""
        base = tmp_path / "skills"
        skill_md = base / "charts" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.touch()
        from agent.skills.discovery import _get_category_from_path
        assert _get_category_from_path(skill_md, base) is None

    def test_different_base_dir_returns_none(self, tmp_path):
        """base_dir mismatch → None without crash"""
        other_base = tmp_path / "other"
        skill_md = tmp_path / "skills" / "cim" / "cim-eda" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.touch()
        from agent.skills.discovery import _get_category_from_path
        assert _get_category_from_path(skill_md, other_base) is None

    def test_category_name_is_directory_name(self, tmp_path):
        """Category name comes from the directory name, not DESCRIPTION.md"""
        base = tmp_path / "skills"
        skill_md = base / "fab-ops" / "fab-ops-analyst" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.touch()
        from agent.skills.discovery import _get_category_from_path
        assert _get_category_from_path(skill_md, base) == "fab-ops"


class TestIterSkillDirs:
    def test_flat_layout_found(self, tmp_path):
        """base/skill-name/SKILL.md is returned"""
        _make_skill(tmp_path, "charts")
        from agent.skills.discovery import iter_skill_dirs
        result = [d.name for d in iter_skill_dirs(tmp_path)]
        assert "charts" in result

    def test_two_level_layout_found(self, tmp_path):
        """base/category/skill-name/SKILL.md is returned"""
        _make_skill(tmp_path, "cim/cim-eda")
        from agent.skills.discovery import iter_skill_dirs
        result = [d.name for d in iter_skill_dirs(tmp_path)]
        assert "cim-eda" in result

    def test_category_dir_itself_not_returned(self, tmp_path):
        """The category directory (no SKILL.md) is NOT returned as a skill dir"""
        _make_skill(tmp_path, "cim/cim-eda")
        from agent.skills.discovery import iter_skill_dirs
        result = [d.name for d in iter_skill_dirs(tmp_path)]
        assert "cim" not in result

    def test_description_md_only_dir_ignored(self, tmp_path):
        """A category dir with only DESCRIPTION.md (no SKILL.md child) is ignored"""
        cat = tmp_path / "empty-cat"
        cat.mkdir()
        (cat / "DESCRIPTION.md").write_text("---\nname: empty-cat\n---\n", encoding="utf-8")
        from agent.skills.discovery import iter_skill_dirs
        result = iter_skill_dirs(tmp_path)
        assert result == []


class TestSkillCatalogForPrompt:
    def test_tier0_header_appears_before_skills(self, tmp_path):
        """Tier 0 category summary appears before individual skill bullets"""
        skills = tmp_path / "skills"
        _make_skill(skills, "cim/cim-eda")
        _make_skill(skills, "general/charts")
        from agent.skills.embeddings import skill_catalog_for_prompt
        catalog = skill_catalog_for_prompt(base_dir=skills)
        tier0_pos = catalog.index("Available skill categories")
        # At least one skill name appears after the Tier 0 header
        skills_header_pos = catalog.index("Available Skills")
        assert tier0_pos < skills_header_pos

    def test_category_description_from_description_md(self, tmp_path):
        """DESCRIPTION.md description field is used in Tier 0 category line"""
        skills = tmp_path / "skills"
        cim = skills / "cim"
        cim.mkdir(parents=True)
        desc = "---\nname: cim\ndescription: CIM 核心查詢技能集\n---\n"
        (cim / "DESCRIPTION.md").write_text(desc, encoding="utf-8")
        _make_skill(skills, "cim/cim-eda")
        from agent.skills.embeddings import skill_catalog_for_prompt
        catalog = skill_catalog_for_prompt(base_dir=skills)
        assert "CIM 核心查詢技能集" in catalog

    def test_category_without_description_md_shows_name(self, tmp_path):
        """Category without DESCRIPTION.md still appears in Tier 0"""
        skills = tmp_path / "skills"
        _make_skill(skills, "fab-ops/fab-ops-analyst")
        from agent.skills.embeddings import skill_catalog_for_prompt
        catalog = skill_catalog_for_prompt(base_dir=skills)
        assert "fab-ops" in catalog

    def test_uncategorised_skills_excluded_from_tier0(self, tmp_path):
        """Flat-layout skills don't appear in the Tier 0 category summary"""
        skills = tmp_path / "skills"
        _make_skill(skills, "charts")          # flat — no category
        _make_skill(skills, "cim/cim-eda")     # categorised
        from agent.skills.embeddings import skill_catalog_for_prompt
        catalog = skill_catalog_for_prompt(base_dir=skills)
        # "Available skill categories" section lists only cim, not charts
        tier0_section = catalog.split("## Available Skills")[0]
        assert "cim" in tier0_section
        assert "charts" not in tier0_section

    def test_skill_count_correct(self, tmp_path):
        """Tier 0 shows the correct skill count per category"""
        skills = tmp_path / "skills"
        for i in range(3):
            _make_skill(skills, f"cim/skill-{i}")
        from agent.skills.embeddings import skill_catalog_for_prompt
        catalog = skill_catalog_for_prompt(base_dir=skills)
        assert "3 skill" in catalog    # "3 skills" or "3 skill(s)"

    def test_empty_skills_dir_returns_empty_string(self, tmp_path):
        """No skills → empty string, no crash"""
        skills = tmp_path / "skills"
        skills.mkdir()
        from agent.skills.embeddings import skill_catalog_for_prompt
        assert skill_catalog_for_prompt(base_dir=skills) == ""


# ══════════════════════════════════════════════════════════════════════════════
# Component 2 — Interface filtering (platforms:)
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillMatchesPlatform:
    def test_no_platforms_field_matches_all(self):
        """No platforms field → matches any platform"""
        from agent.skills.discovery import skill_matches_platform
        assert skill_matches_platform({}, "chat") is True
        assert skill_matches_platform({}, "claude_code") is True
        assert skill_matches_platform({}, None) is True

    def test_platform_in_list_matches(self):
        """Platform in frontmatter list → True"""
        from agent.skills.discovery import skill_matches_platform
        fm = {"platforms": ["chat", "copilot"]}
        assert skill_matches_platform(fm, "chat") is True
        assert skill_matches_platform(fm, "copilot") is True

    def test_platform_not_in_list_excludes(self):
        """Platform not in frontmatter list → False"""
        from agent.skills.discovery import skill_matches_platform
        fm = {"platforms": ["claude_code"]}
        assert skill_matches_platform(fm, "chat") is False
        assert skill_matches_platform(fm, "telegram") is False

    def test_platform_none_always_matches(self):
        """platform=None (no filtering) → always True regardless of platforms field"""
        from agent.skills.discovery import skill_matches_platform
        fm = {"platforms": ["claude_code"]}
        assert skill_matches_platform(fm, None) is True

    def test_empty_platforms_list_matches_all(self):
        """platforms: [] → treated as unset, always True"""
        from agent.skills.discovery import skill_matches_platform
        assert skill_matches_platform({"platforms": []}, "chat") is True


class TestSyncClaudeCodePlatformFilter:
    def _run(self, tmp_path, src, settings, platform="claude_code", dry_run=False):
        """Run _sync_skills with Path.home() pointing at tmp_path/home."""
        settings.AGENT_DISABLED_SKILLS = []
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        home = tmp_path / "home"
        cmd = _make_sync_cmd()
        with patch.object(Path, "home", return_value=home):
            cmd._sync_skills(
                home / ".claude", dry_run=dry_run,
                skills_dir=src, platform=platform,
            )
        return home / ".claude" / "skills", cmd.stdout

    def test_claude_code_only_skill_is_synced(self, tmp_path, settings):
        """platforms: [claude_code] skill is written to ~/.claude/skills/"""
        src = tmp_path / "skills"
        _make_skill(src, "mcp-builder",
                    frontmatter={"name": "mcp-builder", "description": "MCP builder",
                                 "platforms": ["claude_code"]})
        dst, _ = self._run(tmp_path, src, settings)
        assert (dst / "mcp-builder" / "SKILL.md").exists()

    def test_chat_only_skill_excluded_from_claude_code_sync(self, tmp_path, settings):
        """platforms: [chat] skill is NOT written to ~/.claude/skills/"""
        src = tmp_path / "skills"
        _make_skill(src, "fab-ops-analyst",
                    frontmatter={"name": "fab-ops-analyst", "description": "Fab ops",
                                 "platforms": ["chat", "copilot"]})
        dst, _ = self._run(tmp_path, src, settings)
        assert not (dst / "fab-ops-analyst").exists()

    def test_no_platforms_field_always_synced(self, tmp_path, settings):
        """Skills without platforms field always appear in claude_code sync"""
        src = tmp_path / "skills"
        _make_skill(src, "charts", frontmatter={"name": "charts", "description": "Charts"})
        dst, _ = self._run(tmp_path, src, settings)
        assert (dst / "charts" / "SKILL.md").exists()


# ══════════════════════════════════════════════════════════════════════════════
# Component 3 — Tier 3 on-demand supporting files hint
# ══════════════════════════════════════════════════════════════════════════════


class TestAppendSupportingFilesHint:
    def test_no_bundled_dirs_body_unchanged(self, tmp_path):
        """No references/templates/assets → body returned unchanged"""
        from agent.skills.loader import _append_supporting_files_hint
        skill_dir = tmp_path / "charts"
        skill_dir.mkdir()
        body = "## Instructions\n\nDo something."
        result = _append_supporting_files_hint(body, skill_dir, "charts")
        assert result == body

    def test_references_dir_appends_hint(self, tmp_path):
        """references/ present → hint appended after body"""
        from agent.skills.loader import _append_supporting_files_hint
        skill_dir = tmp_path / "cim-router"
        ref = skill_dir / "references"
        ref.mkdir(parents=True)
        (ref / "catalogs.md").write_text("# Catalogs", encoding="utf-8")
        body = "## Instructions"
        result = _append_supporting_files_hint(body, skill_dir, "cim-router")
        assert "[Supporting files available on demand:]" in result
        assert "references/catalogs.md" in result
        assert 'skill_view("cim-router"' in result

    def test_multiple_bundled_dirs_all_listed(self, tmp_path):
        """references/ + assets/ both present → both listed"""
        from agent.skills.loader import _append_supporting_files_hint
        skill_dir = tmp_path / "mcp-builder"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "references" / "api.md").write_text("API", encoding="utf-8")
        (skill_dir / "assets").mkdir()
        (skill_dir / "assets" / "template.json").write_text("{}", encoding="utf-8")
        result = _append_supporting_files_hint("body", skill_dir, "mcp-builder")
        assert "references/api.md" in result
        assert "assets/template.json" in result

    def test_nested_files_listed_with_relative_path(self, tmp_path):
        """references/subdir/file.md → listed as relative path"""
        from agent.skills.loader import _append_supporting_files_hint
        skill_dir = tmp_path / "skill-a"
        nested = skill_dir / "references" / "sub"
        nested.mkdir(parents=True)
        (nested / "deep.md").write_text("Deep", encoding="utf-8")
        result = _append_supporting_files_hint("body", skill_dir, "skill-a")
        assert "references/sub/deep.md" in result

    def test_empty_references_dir_no_hint(self, tmp_path):
        """references/ exists but empty → body unchanged"""
        from agent.skills.loader import _append_supporting_files_hint
        skill_dir = tmp_path / "skill-b"
        (skill_dir / "references").mkdir(parents=True)
        body = "## Instructions"
        result = _append_supporting_files_hint(body, skill_dir, "skill-b")
        assert result == body

    def test_hint_appended_after_body_content(self, tmp_path):
        """Hint always follows body content, never precedes it"""
        from agent.skills.loader import _append_supporting_files_hint
        skill_dir = tmp_path / "skill-c"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "references" / "ref.md").write_text("ref", encoding="utf-8")
        body = "ORIGINAL BODY"
        result = _append_supporting_files_hint(body, skill_dir, "skill-c")
        assert result.index("ORIGINAL BODY") < result.index("[Supporting files")


# ══════════════════════════════════════════════════════════════════════════════
# Component 4 — Skill enable/disable control
# ══════════════════════════════════════════════════════════════════════════════


class TestParsePlatformDisabled:
    def test_single_platform_single_skill(self):
        from agent.skills.discovery import _parse_platform_disabled
        assert _parse_platform_disabled("chat:skill-a") == {"chat": {"skill-a"}}

    def test_single_platform_multiple_skills(self):
        from agent.skills.discovery import _parse_platform_disabled
        result = _parse_platform_disabled("chat:skill-a,skill-b")
        assert result == {"chat": {"skill-a", "skill-b"}}

    def test_multiple_platforms(self):
        from agent.skills.discovery import _parse_platform_disabled
        result = _parse_platform_disabled("chat:skill-a;copilot:skill-b,skill-c")
        assert result["chat"] == {"skill-a"}
        assert result["copilot"] == {"skill-b", "skill-c"}

    def test_empty_string_returns_empty_dict(self):
        from agent.skills.discovery import _parse_platform_disabled
        assert _parse_platform_disabled("") == {}

    def test_malformed_entry_skipped_without_crash(self):
        """Entry without colon is skipped; valid entries still parsed"""
        from agent.skills.discovery import _parse_platform_disabled
        result = _parse_platform_disabled("chat:skill-a;INVALID;copilot:skill-b")
        assert "chat" in result
        assert "copilot" in result


class TestGetDisabledSkills:
    def test_global_disabled_from_settings(self, settings):
        """AGENT_DISABLED_SKILLS returns global disabled set"""
        settings.AGENT_DISABLED_SKILLS = ["skill-creator", "mcp-builder"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        from agent.skills.discovery import get_disabled_skills
        result = get_disabled_skills(platform=None)
        assert result == {"skill-creator", "mcp-builder"}

    def test_platform_specific_overrides_global(self, settings):
        """Platform-specific list replaces global list for that platform"""
        settings.AGENT_DISABLED_SKILLS = ["skill-a"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = "chat:skill-b,skill-c"
        from agent.skills.discovery import get_disabled_skills
        result = get_disabled_skills(platform="chat")
        assert result == {"skill-b", "skill-c"}
        assert "skill-a" not in result

    def test_unknown_platform_falls_back_to_global(self, settings):
        """Platform with no specific entry falls back to global list"""
        settings.AGENT_DISABLED_SKILLS = ["skill-a"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = "chat:skill-b"
        from agent.skills.discovery import get_disabled_skills
        result = get_disabled_skills(platform="telegram")
        assert result == {"skill-a"}

    def test_empty_settings_returns_empty_set(self, settings):
        """No disabled skills configured → empty set"""
        settings.AGENT_DISABLED_SKILLS = []
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        from agent.skills.discovery import get_disabled_skills
        assert get_disabled_skills() == set()


class TestSyncClaudeCodeDisabled:
    def _run(self, tmp_path, src, settings, dry_run=False):
        home = tmp_path / "home"
        cmd = _make_sync_cmd()
        with patch.object(Path, "home", return_value=home):
            cmd._sync_skills(home / ".claude", dry_run=dry_run, skills_dir=src)
        return home / ".claude" / "skills", cmd.stdout

    def test_disabled_skill_not_written_to_claude_skills(self, tmp_path, settings):
        """Globally disabled skill is skipped during sync"""
        settings.AGENT_DISABLED_SKILLS = ["charts"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        src = tmp_path / "workspace" / "skills"
        _make_skill(src, "charts")
        _make_skill(src, "weather")
        dst, _ = self._run(tmp_path, src, settings)
        assert not (dst / "charts").exists()
        assert (dst / "weather" / "SKILL.md").exists()

    def test_disabled_skill_appears_in_dry_run_as_skipped(self, tmp_path, settings):
        """Dry-run output marks disabled skill as skipped"""
        settings.AGENT_DISABLED_SKILLS = ["charts"]
        settings.AGENT_PLATFORM_DISABLED_SKILLS = ""
        src = tmp_path / "workspace" / "skills"
        _make_skill(src, "charts")
        _, stdout = self._run(tmp_path, src, settings, dry_run=True)
        output = stdout.getvalue()
        assert "skipped" in output.lower() or "disabled" in output.lower()

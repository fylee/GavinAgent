"""P0 tests for spec 025 — Skill Sync Anthropic Compliance.

Tests cover:
  - sync_claude_code: frontmatter preservation, bundled dirs, dry-run, BOM handling
  - import_skills: basic copy, --only filter, --dry-run, --no-sync, bundled dirs
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_skill(
    skills_dir: Path,
    name: str,
    frontmatter: dict | None = None,
    body: str = "## Instructions\n\nDo something.",
    subdirs: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Create a skill directory with SKILL.md and optional subdirectories."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    meta = frontmatter or {"name": name, "description": f"{name} skill"}
    fm = yaml.dump(meta, allow_unicode=True, sort_keys=False).rstrip()
    (skill_dir / "SKILL.md").write_text(f"---\n{fm}\n---\n\n{body}", encoding="utf-8")

    if subdirs:
        for subdir_name, files in subdirs.items():
            subdir = skill_dir / subdir_name
            subdir.mkdir()
            for fname, content in files.items():
                (subdir / fname).write_text(content, encoding="utf-8")

    return skill_dir


def _make_sync_cmd() -> "Command":  # noqa: F821
    from agent.management.commands.sync_claude_code import Command

    cmd = Command()
    cmd.stdout = StringIO()
    cmd.stderr = StringIO()
    cmd.style = MagicMock()
    cmd.style.SUCCESS = lambda s: s
    cmd.style.WARNING = lambda s: s
    cmd.style.ERROR = lambda s: s
    return cmd


def _make_import_cmd() -> "Command":  # noqa: F821
    from agent.management.commands.import_skills import Command

    cmd = Command()
    cmd.stdout = StringIO()
    cmd.stderr = StringIO()
    cmd.style = MagicMock()
    cmd.style.SUCCESS = lambda s: s
    cmd.style.WARNING = lambda s: s
    cmd.style.ERROR = lambda s: s
    return cmd


def _run_sync_skills(tmp_path: Path, settings, dry_run: bool = False) -> tuple[Path, StringIO]:
    """Run _sync_skills with patched Path.home() pointing at tmp_path."""
    dst_root = tmp_path / "home"
    cmd = _make_sync_cmd()
    with patch.object(Path, "home", return_value=dst_root):
        cmd._sync_skills(claude_dir=dst_root / ".claude", dry_run=dry_run)
    return dst_root / ".claude" / "skills", cmd.stdout


# ── sync_claude_code: frontmatter preservation ────────────────────────────────


class TestSyncSkillsFrontmatter:
    def test_name_and_description_preserved(self, tmp_path, settings):
        """Basic name and description are written to destination."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "weather", {"name": "weather", "description": "Get the weather"})

        dst, _ = _run_sync_skills(tmp_path, settings)
        result = yaml.safe_load((dst / "weather" / "SKILL.md").read_text().split("---", 2)[1])
        assert result["name"] == "weather"
        assert result["description"] == "Get the weather"

    def test_all_extra_frontmatter_fields_preserved(self, tmp_path, settings):
        """tools, license, compatibility, metadata — none are dropped."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(
            src,
            "mcp-builder",
            {
                "name": "mcp-builder",
                "description": "Build MCP servers",
                "compatibility": "claude-3-5+",
                "tools": ["fab-mcp/list_tools", "fab-mcp/get_hold"],
                "license": "MIT",
                "metadata": {
                    "triggers": ["build mcp", "create server"],
                    "version": "1.2.0",
                },
            },
        )

        dst, _ = _run_sync_skills(tmp_path, settings)
        result = yaml.safe_load((dst / "mcp-builder" / "SKILL.md").read_text().split("---", 2)[1])

        assert result["compatibility"] == "claude-3-5+"
        assert result["tools"] == ["fab-mcp/list_tools", "fab-mcp/get_hold"]
        assert result["license"] == "MIT"
        assert result["metadata"]["triggers"] == ["build mcp", "create server"]
        assert result["metadata"]["version"] == "1.2.0"

    def test_allowed_tools_field_preserved(self, tmp_path, settings):
        """GavinAgent-specific allowed-tools field is preserved unchanged."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(
            src,
            "cim-router",
            {
                "name": "cim-router",
                "description": "Route CIM queries",
                "allowed-tools": ["cim_mcp/query", "cim_mcp/list"],
            },
        )

        dst, _ = _run_sync_skills(tmp_path, settings)
        result = yaml.safe_load((dst / "cim-router" / "SKILL.md").read_text().split("---", 2)[1])
        assert result["allowed-tools"] == ["cim_mcp/query", "cim_mcp/list"]

    def test_missing_description_gets_default(self, tmp_path, settings):
        """Skills without description receive 'GavinAgent skill' as default."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "nodesc", {"name": "nodesc"})

        dst, _ = _run_sync_skills(tmp_path, settings)
        result = yaml.safe_load((dst / "nodesc" / "SKILL.md").read_text().split("---", 2)[1])
        assert result["description"] == "GavinAgent skill"

    def test_body_content_preserved(self, tmp_path, settings):
        """Markdown body after frontmatter is written unchanged."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        body = "## How to use\n\nCall the API.\n\nSome **bold** text."
        _make_skill(src, "myskill", body=body)

        dst, _ = _run_sync_skills(tmp_path, settings)
        dest_text = (dst / "myskill" / "SKILL.md").read_text()
        assert "## How to use" in dest_text
        assert "Some **bold** text." in dest_text

    def test_name_sanitised_for_filesystem(self, tmp_path, settings):
        """Spaces and special chars in name are replaced with dashes."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "my-skill", {"name": "my skill!", "description": "Test"})

        dst, _ = _run_sync_skills(tmp_path, settings)
        # "my skill!" → "my-skill-"  (trailing dash stripped by .strip("-"))
        written_names = [d.name for d in dst.iterdir() if d.is_dir()] if dst.exists() else []
        assert any("my" in n for n in written_names)

    def test_bom_prefix_handled(self, tmp_path, settings):
        """SKILL.md with UTF-8 BOM (Windows editors) is parsed correctly."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        skill_dir = src / "bom-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_bytes(
            b"\xef\xbb\xbf---\nname: bom-skill\ndescription: BOM test\n---\n\nBody text.\n"
        )

        dst, _ = _run_sync_skills(tmp_path, settings)
        result = yaml.safe_load((dst / "bom-skill" / "SKILL.md").read_text().split("---", 2)[1])
        assert result["name"] == "bom-skill"
        assert result["description"] == "BOM test"


# ── sync_claude_code: bundled directories ─────────────────────────────────────


class TestSyncSkillsBundledDirs:
    def test_scripts_dir_copied(self, tmp_path, settings):
        """scripts/ directory is copied to destination."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "fab-ops", subdirs={"scripts": {"run.py": "print('hello')"}})

        dst, _ = _run_sync_skills(tmp_path, settings)
        assert (dst / "fab-ops" / "scripts" / "run.py").exists()
        assert (dst / "fab-ops" / "scripts" / "run.py").read_text() == "print('hello')"

    def test_references_dir_copied(self, tmp_path, settings):
        """references/ directory is copied to destination."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "cim-router", subdirs={"references": {"catalogs.md": "# Catalogs"}})

        dst, _ = _run_sync_skills(tmp_path, settings)
        assert (dst / "cim-router" / "references" / "catalogs.md").exists()
        assert "# Catalogs" in (dst / "cim-router" / "references" / "catalogs.md").read_text()

    def test_assets_dir_copied(self, tmp_path, settings):
        """assets/ directory is copied to destination."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "report-gen", subdirs={"assets": {"template.html": "<html/>"}})

        dst, _ = _run_sync_skills(tmp_path, settings)
        assert (dst / "report-gen" / "assets" / "template.html").exists()

    def test_all_bundled_dirs_copied_together(self, tmp_path, settings):
        """All three bundled dirs are copied when all are present."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(
            src,
            "full-skill",
            subdirs={
                "scripts": {"main.py": "# main"},
                "references": {"api.md": "# API"},
                "assets": {"icon.svg": "<svg/>"},
            },
        )

        dst, _ = _run_sync_skills(tmp_path, settings)
        assert (dst / "full-skill" / "scripts" / "main.py").exists()
        assert (dst / "full-skill" / "references" / "api.md").exists()
        assert (dst / "full-skill" / "assets" / "icon.svg").exists()

    def test_no_bundled_dirs_no_regression(self, tmp_path, settings):
        """Skills without bundled dirs sync cleanly — only SKILL.md written."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "simple")

        dst, _ = _run_sync_skills(tmp_path, settings)
        dest_dir = dst / "simple"
        assert (dest_dir / "SKILL.md").exists()
        assert not (dest_dir / "scripts").exists()
        assert not (dest_dir / "references").exists()
        assert not (dest_dir / "assets").exists()

    def test_incremental_sync_overwrites_existing(self, tmp_path, settings):
        """Repeated sync overwrites existing files."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "updatable", subdirs={"scripts": {"tool.py": "v1"}})

        dst, _ = _run_sync_skills(tmp_path, settings)
        assert (dst / "updatable" / "scripts" / "tool.py").read_text() == "v1"

        (src / "updatable" / "scripts" / "tool.py").write_text("v2")
        _run_sync_skills(tmp_path, settings)
        assert (dst / "updatable" / "scripts" / "tool.py").read_text() == "v2"


# ── sync_claude_code: dry-run ─────────────────────────────────────────────────


class TestSyncSkillsDryRun:
    def test_dry_run_writes_no_files(self, tmp_path, settings):
        """--dry-run produces no files on disk."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "nowrite", subdirs={"scripts": {"a.py": "code"}})

        dst, _ = _run_sync_skills(tmp_path, settings, dry_run=True)
        assert not dst.exists() or not (dst / "nowrite").exists()

    def test_dry_run_reports_bundled_dirs(self, tmp_path, settings):
        """--dry-run output mentions bundled directory names."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(
            src,
            "bundle-skill",
            subdirs={"scripts": {"run.py": "code"}, "references": {"docs.md": "docs"}},
        )

        _, stdout = _run_sync_skills(tmp_path, settings, dry_run=True)
        output = stdout.getvalue()
        assert "scripts" in output
        assert "references" in output

    def test_dry_run_no_subdirs_no_brackets(self, tmp_path, settings):
        """--dry-run output has no brackets for skills without bundled dirs."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "plain")

        _, stdout = _run_sync_skills(tmp_path, settings, dry_run=True)
        output = stdout.getvalue()
        assert "[" not in output


# ── sync_claude_code: multi-skill ─────────────────────────────────────────────


class TestSyncSkillsMultiple:
    def test_all_skills_synced(self, tmp_path, settings):
        """All skill dirs in workspace are synced."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        for name in ["charts", "weather", "data-analysis"]:
            _make_skill(src, name)

        dst, _ = _run_sync_skills(tmp_path, settings)
        assert (dst / "charts" / "SKILL.md").exists()
        assert (dst / "weather" / "SKILL.md").exists()
        assert (dst / "data-analysis" / "SKILL.md").exists()

    def test_dirs_without_skill_md_skipped(self, tmp_path, settings):
        """Directories without SKILL.md are silently skipped."""
        src = tmp_path / "workspace" / "skills"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")

        _make_skill(src, "real-skill")
        (src / "not-a-skill").mkdir()  # no SKILL.md

        dst, _ = _run_sync_skills(tmp_path, settings)
        assert (dst / "real-skill" / "SKILL.md").exists()
        assert not (dst / "not-a-skill").exists()

    def test_missing_workspace_skills_dir_handled(self, tmp_path, settings):
        """When workspace/skills/ does not exist, command writes a warning and returns."""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        # do NOT create the skills/ directory

        cmd = _make_sync_cmd()
        dst_root = tmp_path / "home"
        with patch.object(Path, "home", return_value=dst_root):
            cmd._sync_skills(claude_dir=dst_root / ".claude", dry_run=False)

        assert "skipping" in cmd.stdout.getvalue().lower() or "not found" in cmd.stdout.getvalue().lower()


# ── import_skills: basic behaviour ────────────────────────────────────────────


class TestImportSkillsBasic:
    def test_imports_skill_with_skill_md(self, tmp_path, settings):
        """Skills with SKILL.md are copied to workspace/skills/."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        _make_skill(source, "cim-eda")

        _make_import_cmd().handle(source=str(source), only=None, dry_run=False, no_sync=True)

        assert (tmp_path / "workspace" / "skills" / "cim-eda" / "SKILL.md").exists()

    def test_skips_dirs_without_skill_md(self, tmp_path, settings):
        """Source dirs without SKILL.md are skipped; message printed to stdout."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        (source / "not-a-skill").mkdir(parents=True)
        _make_skill(source, "valid-skill")

        cmd = _make_import_cmd()
        cmd.handle(source=str(source), only=None, dry_run=False, no_sync=True)

        ws = tmp_path / "workspace" / "skills"
        assert (ws / "valid-skill" / "SKILL.md").exists()
        assert not (ws / "not-a-skill").exists()
        assert "no SKILL.md" in cmd.stdout.getvalue()

    def test_bundled_dirs_copied_with_skill(self, tmp_path, settings):
        """scripts/, references/, assets/ are all copied with the skill."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        _make_skill(
            source,
            "full-skill",
            subdirs={
                "scripts": {"main.py": "# main"},
                "references": {"api.md": "# API Docs"},
                "assets": {"logo.svg": "<svg/>"},
            },
        )

        _make_import_cmd().handle(source=str(source), only=None, dry_run=False, no_sync=True)

        ws = tmp_path / "workspace" / "skills" / "full-skill"
        assert (ws / "scripts" / "main.py").exists()
        assert (ws / "references" / "api.md").exists()
        assert (ws / "assets" / "logo.svg").exists()

    def test_source_not_found_writes_error(self, tmp_path, settings):
        """Missing source directory writes error to stderr and returns gracefully."""
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        cmd = _make_import_cmd()
        cmd.handle(
            source=str(tmp_path / "nonexistent"),
            only=None,
            dry_run=False,
            no_sync=True,
        )

        assert "not found" in cmd.stderr.getvalue().lower() or "Source not found" in cmd.stderr.getvalue()


# ── import_skills: --only filter ──────────────────────────────────────────────


class TestImportSkillsOnlyFilter:
    def test_only_imports_named_skills(self, tmp_path, settings):
        """--only limits import to named skills."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        for name in ["skill-a", "skill-b", "skill-c"]:
            _make_skill(source, name)

        _make_import_cmd().handle(
            source=str(source), only=["skill-a", "skill-c"], dry_run=False, no_sync=True
        )

        ws = tmp_path / "workspace" / "skills"
        assert (ws / "skill-a" / "SKILL.md").exists()
        assert not (ws / "skill-b").exists()
        assert (ws / "skill-c" / "SKILL.md").exists()

    def test_only_unknown_name_imports_nothing(self, tmp_path, settings):
        """--only with non-existent name imports nothing."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        ws = tmp_path / "workspace" / "skills"
        ws.mkdir(parents=True)

        _make_skill(source, "real-skill")

        _make_import_cmd().handle(
            source=str(source), only=["ghost-skill"], dry_run=False, no_sync=True
        )

        assert not (ws / "real-skill").exists()


# ── import_skills: --dry-run ───────────────────────────────────────────────────


class TestImportSkillsDryRun:
    def test_dry_run_writes_no_files(self, tmp_path, settings):
        """--dry-run produces no files on disk."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        _make_skill(source, "weather", subdirs={"scripts": {"run.py": "code"}})

        _make_import_cmd().handle(source=str(source), only=None, dry_run=True, no_sync=True)

        assert not (tmp_path / "workspace" / "skills" / "weather").exists()

    def test_dry_run_reports_skill_name_and_subdirs(self, tmp_path, settings):
        """--dry-run output includes skill names and bundled dir names."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        _make_skill(
            source, "mcp-builder", subdirs={"scripts": {"run.py": "code"}}
        )

        cmd = _make_import_cmd()
        cmd.handle(source=str(source), only=None, dry_run=True, no_sync=True)

        output = cmd.stdout.getvalue()
        assert "mcp-builder" in output
        assert "scripts" in output

    def test_dry_run_does_not_trigger_sync(self, tmp_path, settings):
        """--dry-run never calls sync_claude_code even without --no-sync."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        _make_skill(source, "some-skill")

        with patch("agent.management.commands.sync_claude_code.Command") as MockSync:
            _make_import_cmd().handle(
                source=str(source), only=None, dry_run=True, no_sync=False
            )
            MockSync.assert_not_called()


# ── import_skills: --no-sync ──────────────────────────────────────────────────


class TestImportSkillsNoSync:
    def test_no_sync_skips_sync_command(self, tmp_path, settings):
        """--no-sync does not invoke sync_claude_code."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        _make_skill(source, "test-skill")

        with patch("agent.management.commands.sync_claude_code.Command") as MockSync:
            _make_import_cmd().handle(
                source=str(source), only=None, dry_run=False, no_sync=True
            )
            MockSync.assert_not_called()

    def test_default_invokes_sync(self, tmp_path, settings):
        """Without --no-sync, sync_claude_code is instantiated and executed."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        (tmp_path / "workspace" / "skills").mkdir(parents=True)

        _make_skill(source, "test-skill")

        with patch("agent.management.commands.sync_claude_code.Command") as MockSync:
            mock_instance = MagicMock()
            MockSync.return_value = mock_instance

            _make_import_cmd().handle(
                source=str(source), only=None, dry_run=False, no_sync=False
            )

            MockSync.assert_called_once()
            mock_instance.execute.assert_called_once()


# ── import_skills: incremental / overwrite ────────────────────────────────────


class TestImportSkillsIncremental:
    def test_existing_files_overwritten(self, tmp_path, settings):
        """Re-importing overwrites existing workspace files."""
        source = tmp_path / "source"
        settings.AGENT_WORKSPACE_DIR = str(tmp_path / "workspace")
        ws = tmp_path / "workspace" / "skills"
        ws.mkdir(parents=True)

        _make_skill(source, "my-skill", frontmatter={"name": "my-skill", "description": "v1"})

        _make_import_cmd().handle(source=str(source), only=None, dry_run=False, no_sync=True)
        assert "v1" in (ws / "my-skill" / "SKILL.md").read_text()

        (source / "my-skill" / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: v2\n---\n\nUpdated body."
        )
        _make_import_cmd().handle(source=str(source), only=None, dry_run=False, no_sync=True)
        assert "v2" in (ws / "my-skill" / "SKILL.md").read_text()

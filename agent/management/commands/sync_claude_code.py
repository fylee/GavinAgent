"""
Sync GavinAgent resources to Claude Code configuration.

Exports:
  - MCPServer records  →  ~/.claude.json  (mcpServers section, keyed by project path)
  - Workspace skills   →  .claude/commands/<name>.md  (slash commands)
  - Domain-knowledge skills  →  .claude/skills/  (for CLAUDE.md reference)

Usage:
    python manage.py sync_claude_code
    python manage.py sync_claude_code --mcp-only
    python manage.py sync_claude_code --skills-only
    python manage.py sync_claude_code --dry-run
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sync MCP servers and skills from GavinAgent to Claude Code config."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--mcp-only", action="store_true", help="Only export MCP servers")
        parser.add_argument("--skills-only", action="store_true", help="Only export skills")
        parser.add_argument("--dry-run", action="store_true", help="Print changes without writing files")
        parser.add_argument(
            "--claude-dir",
            default=".claude",
            help="Path to the .claude directory (default: .claude relative to manage.py)",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        mcp_only: bool = options["mcp_only"]
        skills_only: bool = options["skills_only"]

        base_dir = Path(settings.BASE_DIR)
        claude_dir = (base_dir / options["claude_dir"]).resolve()

        if not dry_run:
            claude_dir.mkdir(exist_ok=True)
            (claude_dir / "commands").mkdir(exist_ok=True)

        if not skills_only:
            self._sync_mcp(claude_dir, dry_run)
        if not mcp_only:
            self._sync_skills(claude_dir, dry_run)

    # ── MCP servers ────────────────────────────────────────────────────────

    def _sync_mcp(self, claude_dir: Path, dry_run: bool) -> None:
        from agent.mcp.config import load_servers

        servers = {
            name: cfg
            for name, cfg in load_servers().items()
            if cfg.enabled
        }
        if not servers:
            self.stdout.write("  MCP: no active servers found — skipping")
            return

        mcp_entries: dict = {}
        for name, cfg in servers.items():
            entry: dict = {}
            if cfg.type == "sse":
                entry["type"] = "sse"
                entry["url"] = cfg.url
                if cfg.headers:
                    entry["headers"] = cfg.headers
            else:  # stdio
                import shlex
                parts = shlex.split(cfg.command) if cfg.command else []
                entry["type"] = "stdio"
                entry["command"] = parts[0] if parts else cfg.command
                # Prefer explicit args list; fall back to command tail
                if cfg.args:
                    entry["args"] = cfg.args
                elif len(parts) > 1:
                    entry["args"] = parts[1:]
                if cfg.env:
                    entry["env"] = cfg.env

            mcp_entries[name] = entry
            self.stdout.write(f"  MCP [{cfg.type}]: {name}")

        # Claude Code CLI stores project MCP config in ~/.claude.json keyed by project path.
        # On Windows, manage.py may resolve through symlinks (e.g. C:\Users\fylee\D\...)
        # while Claude Code records the real drive path (D:\...). We match on the last
        # 3 path components which are stable across both representations.
        cwd_key = str(Path.cwd().resolve()).replace("\\", "/")
        cwd_tail = "/".join(Path(cwd_key).parts[-3:]).lower()
        claude_json_path = Path.home() / ".claude.json"

        claude_json: dict = {}
        if claude_json_path.exists():
            try:
                claude_json = json.loads(claude_json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        projects: dict = claude_json.setdefault("projects", {})

        # Find existing project entry — match on trailing path components to handle
        # symlinked / differently-cased drive letters on Windows.
        def _tail(p: str) -> str:
            return "/".join(Path(p.replace("\\", "/")).parts[-3:]).lower()

        matched_key = next((k for k in projects if _tail(k) == cwd_tail), None)
        if matched_key is None:
            matched_key = cwd_key
            projects[matched_key] = {}

        projects[matched_key]["mcpServers"] = mcp_entries
        content = json.dumps(claude_json, indent=2, ensure_ascii=False)

        if dry_run:
            self.stdout.write(
                f"\n--- would write {claude_json_path} [project: {matched_key}] ---\n"
                + json.dumps({"mcpServers": mcp_entries}, indent=2)
                + "\n"
            )
        else:
            claude_json_path.write_text(content, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"  Written: {claude_json_path} [project: {matched_key}]"))

    # ── Skills → ~/.claude/skills/ ────────────────────────────────────────

    def _sync_skills(
        self,
        claude_dir: Path,
        dry_run: bool,
        skills_dir: Path | None = None,
        platform: str = "claude_code",
    ) -> None:
        """
        Sync workspace skills to ~/.claude/skills/.

        Spec 026: Accepts optional `skills_dir` override (for testing) and
        `platform` parameter (default "claude_code") to filter by `platforms:`
        frontmatter field and disabled skill settings.
        """
        from agent.skills.discovery import skill_matches_platform, get_disabled_skills

        if skills_dir is None:
            skills_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
        if not skills_dir.exists():
            self.stdout.write("  Skills: workspace/skills/ not found — skipping")
            return

        # Spec 026: get disabled set for claude_code platform
        disabled = get_disabled_skills(platform=platform)

        # Skills must live in ~/.claude/skills/<name>/SKILL.md to be resolvable
        # by the Claude Code CLI skill system (not .claude/commands/).
        user_skills_dir = Path.home() / ".claude" / "skills"
        written = 0

        from agent.skills.discovery import iter_skill_dirs
        for skill_dir in iter_skill_dirs(skills_dir):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            text = skill_md.read_text(encoding="utf-8-sig")
            meta: dict = {}
            body = text
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
                    body = parts[2].strip()

            name: str = meta.get("name", skill_dir.name)

            # Sanitise name for filesystem (replace spaces/special chars with dashes)
            safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-")

            # Spec 026: platform filtering — skip if `platforms:` excludes claude_code
            if not skill_matches_platform(meta, platform):
                if dry_run:
                    self.stdout.write(f"  Skills: {name}  [skipped — platform not {platform}]")
                continue

            # Spec 026: disabled skill filtering
            if safe_name in disabled or name in disabled:
                if dry_run:
                    self.stdout.write(f"  Skills: {name}  [skipped — disabled]")
                continue

            # Merge strategy: normalise name only, preserve all other frontmatter fields
            meta["name"] = safe_name
            if "description" not in meta:
                meta["description"] = "GavinAgent skill"
            output_frontmatter = yaml.dump(meta, allow_unicode=True, sort_keys=False).rstrip()
            skill_content = f"---\n{output_frontmatter}\n---\n\n{body}"

            dest_dir = user_skills_dir / safe_name
            dest = dest_dir / "SKILL.md"

            # Identify bundled resource directories present in the source
            bundled_dirs = ["scripts", "references", "assets"]
            present_subdirs = [d for d in bundled_dirs if (skill_dir / d).is_dir()]

            if dry_run:
                subdir_note = f"  [{', '.join(present_subdirs)}]" if present_subdirs else ""
                self.stdout.write(f"  Skills: would write {dest} ({len(skill_content)} chars){subdir_note}")
            else:
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest.write_text(skill_content, encoding="utf-8")
                for subdir_name in present_subdirs:
                    src_sub = skill_dir / subdir_name
                    dst_sub = dest_dir / subdir_name
                    shutil.copytree(src_sub, dst_sub, dirs_exist_ok=True)
                self.stdout.write(f"  Skills: {name}  →  /{safe_name}" + (f"  [{', '.join(present_subdirs)}]" if present_subdirs else ""))
                written += 1

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"  Written {written} skill(s) to {user_skills_dir}"))

"""
Import skills from the Winbond skills repository into GavinAgent's workspace,
then optionally sync to ~/.claude/skills/ via sync_claude_code.

Source: <BASE_DIR>/../skills/.agents/skills/   (override with --source)
Dest:   <AGENT_WORKSPACE_DIR>/skills/

Usage:
    python manage.py import_skills
    python manage.py import_skills --source ../skills/.agents/skills/
    python manage.py import_skills --only mcp-builder cim-router
    python manage.py import_skills --dry-run
    python manage.py import_skills --no-sync
"""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Import skills from the Winbond skills repo into GavinAgent workspace."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source",
            default=None,
            help=(
                "Path to the source skills directory "
                "(default: ../skills/.agents/skills/ relative to BASE_DIR)"
            ),
        )
        parser.add_argument(
            "--only",
            nargs="+",
            metavar="NAME",
            help="Import only the named skill(s)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be copied without writing files",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Skip the sync_claude_code step after importing",
        )

    def handle(self, *args, **options) -> None:
        base_dir = Path(settings.BASE_DIR)

        if options["source"]:
            source_dir = Path(options["source"]).resolve()
        else:
            source_dir = (base_dir / ".." / "skills" / ".agents" / "skills").resolve()

        dest_dir = Path(settings.AGENT_WORKSPACE_DIR) / "skills"
        dry_run: bool = options["dry_run"]
        only: list[str] | None = options["only"]

        if not source_dir.exists():
            self.stderr.write(
                self.style.ERROR(f"Source not found: {source_dir}")
            )
            return

        self.stdout.write(f"Source: {source_dir}")
        self.stdout.write(f"Dest:   {dest_dir}")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run mode — no files will be written"))

        imported = 0
        skipped = 0

        for skill_dir in sorted(source_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            if not (skill_dir / "SKILL.md").exists():
                self.stdout.write(f"  Skip {skill_dir.name} (no SKILL.md)")
                skipped += 1
                continue
            if only and skill_dir.name not in only:
                continue

            dest = dest_dir / skill_dir.name
            subdirs = [d.name for d in sorted(skill_dir.iterdir()) if d.is_dir()]

            if dry_run:
                subdir_note = f"  [{', '.join(subdirs)}]" if subdirs else ""
                self.stdout.write(f"  Would copy: {skill_dir.name}/{subdir_note}")
            else:
                shutil.copytree(skill_dir, dest, dirs_exist_ok=True)
                subdir_note = f"  [{', '.join(subdirs)}]" if subdirs else ""
                self.stdout.write(f"  Imported: {skill_dir.name}{subdir_note}")
                imported += 1

        if dry_run:
            return

        self.stdout.write(
            self.style.SUCCESS(f"Imported {imported} skill(s) ({skipped} skipped — no SKILL.md)")
        )

        if not options["no_sync"]:
            self.stdout.write("\nRunning sync_claude_code --skills-only ...")
            from agent.management.commands.sync_claude_code import Command as SyncCmd

            sync = SyncCmd()
            sync.stdout = self.stdout
            sync.stderr = self.stderr
            sync.style = self.style
            sync.execute(
                skills_only=True,
                mcp_only=False,
                dry_run=False,
                claude_dir=".claude",
            )

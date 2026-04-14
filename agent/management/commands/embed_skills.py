"""
Management command: embed_skills

Spec 023: Embeds skills from all trusted source directories into SkillEmbedding.

Usage:
    python manage.py embed_skills                  # embed new/changed skills only
    python manage.py embed_skills --force          # re-embed all regardless of hash
    python manage.py embed_skills --skill weather  # embed one skill only
    python manage.py embed_skills --dry-run        # print decisions without writing
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Embed skills from all trusted source directories into SkillEmbedding."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-embed all skills regardless of content hash.",
        )
        parser.add_argument(
            "--skill",
            metavar="NAME",
            help="Embed a single named skill only.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be embedded without writing to the database.",
        )

    def handle(self, *args, **options):
        force: bool = options["force"]
        single_skill: str | None = options["skill"]
        dry_run: bool = options["dry_run"]

        from agent.skills.discovery import all_skill_dirs, iter_skill_dirs, _read_skill_name
        from agent.skills.embeddings import _embed_skill_dir, _content_hash, _skill_embed_text, _parse_metadata_list
        from agent.models import SkillEmbedding

        import yaml

        sources = all_skill_dirs(check_db_trust=True)
        seen_names: set[str] = set()

        counts = {"embedded": 0, "skipped_unchanged": 0, "skipped_untrusted": 0, "skipped_dry_run": 0, "failed": 0}

        for src in sources:
            if not src.trusted:
                skill_dirs = list(iter_skill_dirs(src.path))
                n_untrusted = len(skill_dirs)
                if n_untrusted:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping untrusted skill source: {src.path} "
                            f"({n_untrusted} skill(s)) — approve it in the Skills UI first"
                        )
                    )
                    counts["skipped_untrusted"] += n_untrusted
                continue

            for skill_dir in iter_skill_dirs(src.path):
                skill_md = skill_dir / "SKILL.md"
                name = _read_skill_name(skill_md)

                if name in seen_names:
                    continue  # shadowed by higher-precedence dir
                seen_names.add(name)

                if single_skill and name != single_skill:
                    continue

                if force and not dry_run:
                    # Clear hash so _embed_skill_dir always re-embeds
                    SkillEmbedding.objects.filter(skill_name=name).update(content_hash="")

                if dry_run:
                    # Check if it would actually change
                    try:
                        text = skill_md.read_text(encoding="utf-8-sig")
                        meta: dict = {}
                        body = text
                        if text.startswith("---"):
                            parts = text.split("---", 2)
                            if len(parts) >= 3:
                                meta = yaml.safe_load(parts[1]) or {}
                                body = parts[2].strip()
                        nested_meta = meta.get("metadata", {}) or {}
                        examples = _parse_metadata_list(nested_meta, "examples") or _parse_metadata_list(meta, "examples")
                        triggers = _parse_metadata_list(nested_meta, "triggers") or _parse_metadata_list(meta, "triggers")
                        description = meta.get("description", "")
                        embed_input = _skill_embed_text(name, description, body, examples, triggers)
                        chash = _content_hash(embed_input)
                        existing = SkillEmbedding.objects.filter(skill_name=name).first()
                        if force or not existing or existing.content_hash != chash:
                            self.stdout.write(f"  [would embed] {name}  ({src.path})")
                            counts["skipped_dry_run"] += 1
                        else:
                            self.stdout.write(f"  [unchanged]   {name}")
                            counts["skipped_unchanged"] += 1
                    except Exception as exc:
                        self.stdout.write(self.style.ERROR(f"  [error]       {name}: {exc}"))
                        counts["failed"] += 1
                    continue

                # Real embed
                try:
                    result = _embed_skill_dir(skill_dir)
                    if result:
                        self.stdout.write(self.style.SUCCESS(f"  Embedded: {name}"))
                        counts["embedded"] += 1
                    else:
                        self.stdout.write(f"  Unchanged: {name}")
                        counts["skipped_unchanged"] += 1
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"  Failed:   {name}: {exc}"))
                    counts["failed"] += 1

        if single_skill and not any(single_skill in str(s) for s in seen_names):
            self.stdout.write(self.style.WARNING(f"Skill '{single_skill}' not found in any trusted source."))

        # Summary
        if dry_run:
            self.stdout.write(
                f"\nDry run: would embed {counts['skipped_dry_run']}, "
                f"unchanged {counts['skipped_unchanged']}, "
                f"untrusted {counts['skipped_untrusted']}, "
                f"failed {counts['failed']}"
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDone: embedded {counts['embedded']}, "
                    f"skipped {counts['skipped_unchanged']} unchanged, "
                    f"skipped {counts['skipped_untrusted']} untrusted, "
                    f"failed {counts['failed']}"
                )
            )

"""WorkflowLoader — scans workspace/workflows/*.yml and syncs DB + Celery Beat."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

def _workflows_dir() -> Path:
    return Path(settings.AGENT_WORKSPACE_DIR) / "workflows"


def _parse_yaml(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning("Workflow %s: not a dict", path.name)
            return None
        for required in ("name", "trigger", "steps"):
            if required not in data:
                logger.warning("Workflow %s: missing required field '%s'", path.name, required)
                return None
        if not isinstance(data.get("steps"), list) or not data["steps"]:
            logger.warning("Workflow %s: 'steps' must be a non-empty list", path.name)
            return None
        return data
    except Exception as exc:
        logger.error("Failed to parse workflow %s: %s", path, exc)
        return None


def _ensure_crontab(cron_expr: str, timezone_name: str = "UTC"):
    from django_celery_beat.models import CrontabSchedule
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Invalid cron expression: {cron_expr!r}")
    minute, hour, day_of_month, month_of_year, day_of_week = fields
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
        timezone=timezone_name,
    )
    return schedule


def _ensure_interval(minutes: int):
    from django_celery_beat.models import IntervalSchedule
    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=minutes,
        period=IntervalSchedule.MINUTES,
    )
    return schedule


def _ensure_clocked(dt_str: str):
    from django_celery_beat.models import ClockedSchedule
    from dateutil.parser import parse as parse_dt
    clocked_time = parse_dt(dt_str)
    schedule, _ = ClockedSchedule.objects.get_or_create(clocked_time=clocked_time)
    return schedule


def _register_periodic_task(workflow) -> int | None:
    """Create or update a PeriodicTask for the workflow. Returns PeriodicTask pk."""
    from django_celery_beat.models import PeriodicTask
    import json

    trigger = workflow.definition.get("trigger", {})
    task_name = f"workflow:{workflow.name}"
    task_kwargs = json.dumps({"workflow_id": str(workflow.id)})

    try:
        if "cron" in trigger:
            schedule = _ensure_crontab(trigger["cron"], trigger.get("timezone", "UTC"))
            pt, _ = PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults=dict(
                    task="agent.tasks.execute_workflow",
                    crontab=schedule,
                    interval=None,
                    clocked=None,
                    one_off=False,
                    enabled=workflow.enabled,
                    kwargs=task_kwargs,
                ),
            )
        elif "interval_minutes" in trigger:
            schedule = _ensure_interval(int(trigger["interval_minutes"]))
            pt, _ = PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults=dict(
                    task="agent.tasks.execute_workflow",
                    interval=schedule,
                    crontab=None,
                    clocked=None,
                    one_off=False,
                    enabled=workflow.enabled,
                    kwargs=task_kwargs,
                ),
            )
        elif "at" in trigger:
            schedule = _ensure_clocked(trigger["at"])
            pt, _ = PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults=dict(
                    task="agent.tasks.execute_workflow",
                    clocked=schedule,
                    crontab=None,
                    interval=None,
                    one_off=True,
                    enabled=workflow.enabled,
                    kwargs=task_kwargs,
                ),
            )
        else:
            logger.warning("Workflow %s: unknown trigger type", workflow.name)
            return None
        return pt.pk
    except Exception as exc:
        logger.error("Failed to register PeriodicTask for %s: %s", workflow.name, exc)
        return None


def _delete_periodic_task(name: str) -> None:
    from django_celery_beat.models import PeriodicTask
    PeriodicTask.objects.filter(name=f"workflow:{name}").delete()


class WorkflowLoader:
    def load_all(self) -> list[str]:
        """Scan workflows dir, sync DB and Celery Beat. Returns list of loaded names."""
        from agent.models import Workflow, Agent

        wf_dir = _workflows_dir()
        wf_dir.mkdir(parents=True, exist_ok=True)

        loaded_names: list[str] = []
        yml_files = list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))

        for yml_path in yml_files:
            data = _parse_yaml(yml_path)
            if data is None:
                continue

            name = data["name"]
            description = data.get("description", "")
            enabled = data.get("enabled", True)
            delivery = data.get("delivery", "announce")

            # Resolve agent
            agent_name = data.get("agent")
            agent = None
            if agent_name and agent_name != "default":
                agent = Agent.objects.filter(name=agent_name, is_active=True).first()
            if agent is None:
                agent = Agent.objects.filter(is_default=True, is_active=True).first()

            workflow, created = Workflow.objects.update_or_create(
                name=name,
                defaults=dict(
                    description=description,
                    agent=agent,
                    enabled=enabled,
                    definition=data,
                    filename=str(yml_path.relative_to(wf_dir.parent)),
                    delivery=delivery,
                ),
            )

            beat_id = _register_periodic_task(workflow)
            if beat_id and beat_id != workflow.celery_beat_id:
                workflow.celery_beat_id = beat_id
                workflow.save(update_fields=["celery_beat_id"])

            loaded_names.append(name)
            logger.info("Loaded workflow '%s' (created=%s)", name, created)

        # Remove stale PeriodicTasks for workflows no longer in files
        existing_names = set(Workflow.objects.values_list("name", flat=True))
        stale_names = existing_names - set(loaded_names)
        for stale in stale_names:
            _delete_periodic_task(stale)
            Workflow.objects.filter(name=stale).delete()
            logger.info("Removed stale workflow '%s'", stale)

        return loaded_names

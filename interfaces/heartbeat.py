from celery import shared_task


@shared_task
def heartbeat_agent_run(agent_name: str, input_text: str):
    """Triggered by Celery Beat to run an agent on a schedule."""
    import logging
    from agent.models import Agent, AgentRun
    from agent.tasks import execute_agent_run

    logger = logging.getLogger(__name__)
    try:
        agent = Agent.objects.get(name=agent_name, is_active=True)
    except Agent.DoesNotExist:
        logger.warning(f"Heartbeat: agent '{agent_name}' not found.")
        return

    run = AgentRun.objects.create(
        agent=agent,
        trigger_source=AgentRun.TriggerSource.HEARTBEAT,
        input=input_text,
    )
    execute_agent_run.delay(str(run.id))
    logger.info(f"Heartbeat triggered run {run.id} for agent '{agent_name}'")

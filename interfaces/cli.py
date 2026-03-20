import click
import os
import django


def setup_django():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    django.setup()


@click.group()
def cli():
    """AI Assistant CLI"""
    pass


@cli.command()
@click.argument("message")
@click.option("--conversation-id", "-c", default=None, help="Continue an existing conversation.")
def chat(message: str, conversation_id: str | None):
    """Send a message to the chat assistant."""
    setup_django()
    from chat.models import Conversation
    from chat.services import ChatService

    if conversation_id:
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            click.echo(f"Conversation {conversation_id} not found.", err=True)
            raise SystemExit(1)
    else:
        conversation = Conversation.objects.create(
            interface=Conversation.Interface.CLI,
            title=f"CLI: {message[:40]}",
        )
        click.echo(f"Started conversation: {conversation.id}")

    service = ChatService(conversation)
    reply = service.send_message(message)
    click.echo(f"\nAssistant: {reply.content}")


@cli.command()
@click.argument("agent_name")
@click.argument("input_text")
def run_agent(agent_name: str, input_text: str):
    """Trigger an agent run."""
    setup_django()
    from agent.models import Agent, AgentRun
    from agent.tasks import execute_agent_run

    try:
        agent = Agent.objects.get(name=agent_name, is_active=True)
    except Agent.DoesNotExist:
        click.echo(f"Agent '{agent_name}' not found or inactive.", err=True)
        raise SystemExit(1)

    run = AgentRun.objects.create(
        agent=agent,
        trigger_source=AgentRun.TriggerSource.CLI,
        input=input_text,
    )
    execute_agent_run.delay(str(run.id))
    click.echo(f"Agent run started: {run.id}")


if __name__ == "__main__":
    cli()

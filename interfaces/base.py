from dataclasses import dataclass, field
from enum import Enum


class InterfaceType(str, Enum):
    WEB = "web"
    TELEGRAM = "telegram"
    CLI = "cli"
    HEARTBEAT = "heartbeat"


@dataclass
class InboundEvent:
    interface: InterfaceType
    content: str
    external_id: str = ""           # telegram chat_id, CLI session, etc.
    conversation_id: str | None = None
    agent_name: str | None = None   # if routing to agent app
    metadata: dict = field(default_factory=dict)

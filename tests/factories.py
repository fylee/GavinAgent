"""factory_boy factories for test data."""
from __future__ import annotations

import uuid

import factory
from django.utils import timezone


class AgentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.Agent"

    name = factory.Sequence(lambda n: f"Test Agent {n}")
    is_active = True
    model = "openai/gpt-4o-mini"
    tools = factory.LazyFunction(lambda: ["web_read", "api_get", "web_search"])


class ConversationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "chat.Conversation"

    title = factory.Sequence(lambda n: f"Test Conversation {n}")
    interface = "web"


class MessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "chat.Message"

    conversation = factory.SubFactory(ConversationFactory)
    role = "user"
    content = factory.Sequence(lambda n: f"Test message {n}")


class AgentRunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.AgentRun"

    agent = factory.SubFactory(AgentFactory)
    conversation = factory.SubFactory(ConversationFactory)
    input = "test input"
    status = "pending"
    trigger_source = "web"


class ToolExecutionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.ToolExecution"

    run = factory.SubFactory(AgentRunFactory)
    tool_name = "web_read"
    input = factory.LazyFunction(lambda: {"url": "https://example.com"})
    status = "success"


class SkillFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.Skill"

    name = factory.Sequence(lambda n: f"test_skill_{n}")
    enabled = True


class KnowledgeDocumentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.KnowledgeDocument"

    title = factory.Sequence(lambda n: f"Test Document {n}")
    source_type = "text"
    raw_content = "This is test content for the knowledge document."
    status = "ready"
    is_active = True
    chunk_count = 0


class DocumentChunkFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.DocumentChunk"

    document = factory.SubFactory(KnowledgeDocumentFactory)
    content = factory.Sequence(lambda n: f"Test chunk content {n}")
    embedding = factory.LazyFunction(lambda: [0.01] * 1536)
    chunk_index = factory.Sequence(lambda n: n)
    token_count = 50
    content_hash = factory.LazyFunction(lambda: uuid.uuid4().hex)


class LLMUsageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.LLMUsage"
        rename = {"model_name": "model"}

    model_name = "openai/gpt-4o-mini"
    prompt_tokens = 100
    completion_tokens = 50
    total_tokens = 150
    estimated_cost_usd = 0.001
    source = "agent"


class WorkflowFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.Workflow"

    name = factory.Sequence(lambda n: f"Test Workflow {n}")
    agent = factory.SubFactory(AgentFactory)
    enabled = True
    delivery = "silent"
    definition = factory.LazyFunction(lambda: {"steps": [{"input": "test"}]})
    filename = factory.LazyAttribute(lambda o: f"{o.name}.yml")


class MCPServerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.MCPServer"

    name = factory.Sequence(lambda n: f"test-mcp-server-{n}")
    transport = "stdio"
    command = "echo hello"
    enabled = True
    connection_status = "connected"


class HeartbeatLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "agent.HeartbeatLog"

    triggered_at = factory.LazyFunction(timezone.now)
    status = "ok"
    actions_taken = factory.LazyFunction(list)

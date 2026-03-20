from agent.skills.loader import SkillLoader
from agent.skills.registry import SkillRegistry

__all__ = ["SkillLoader", "SkillRegistry"]

# Global registry instance
registry = SkillRegistry()

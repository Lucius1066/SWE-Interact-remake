from .user_agent import UserAgent, UserDecision, UserPersona

__all__ = [
    "UserAgent",
    "UserDecision",
    "UserPersona",
    "UserEnabledTerminus2",
]

# UserEnabledClaudeCode / UserEnabledCodex are not
# re-exported here — they're lazy-loaded by runner.py via import_path so
# that importing this package doesn't pull in harbor (which has a heavier
# transitive dep graph). Use the explicit module path:
#   from user_agent.agents.user_enabled_codex import UserEnabledCodex


def __getattr__(name):
    """Keep the Terminus wrapper import-compatible without eagerly loading Harbor."""
    if name == "UserEnabledTerminus2":
        from .agents.user_enabled_agent import UserEnabledTerminus2

        return UserEnabledTerminus2
    raise AttributeError(name)

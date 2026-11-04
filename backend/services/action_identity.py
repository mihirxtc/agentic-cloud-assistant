"""Audit identity inherited by HTTP tool calls and their worker threads."""
from contextvars import ContextVar

actor = ContextVar('aca_action_actor', default={'operator': 'local-process', 'kind': 'stdio'})

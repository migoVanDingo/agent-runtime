"""Sub-agent tools — each ``BaseTool`` wraps a ``SubAgentSpec`` and dispatches
through ``SubAgentRunner``. Tools live in this subpackage so the registry can
narrow the tool list cleanly when a child agent is built (the runner filters
``SubAgentTool`` instances out of the child's registry to enforce the
no-recursion rule).
"""

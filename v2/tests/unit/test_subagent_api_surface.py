"""Verify the v0.1 public surface is exactly what we promise.

If this test fails, an out-of-tree sub-agent package will break. Bump
__api_version__ accordingly and document the change.
"""
from __future__ import annotations


def test_v01_surface_complete():
    import arc.subagent_api as api

    expected = {
        "__api_version__",
        "SubAgentBuildContext",
        "SubAgentError",
        "SubAgentRecursionError",
        "SubAgentResult",
        "SubAgentSpec",
        "SubAgentTimeoutError",
    }
    assert set(api.__all__) == expected


def test_api_version_is_0_2():
    """v0.2 added SubAgentSpec.params (additive). v0.1 specs still work."""
    from arc.subagent_api import __api_version__
    assert __api_version__ == (0, 2)


def test_runner_and_registry_not_public():
    """Runner/Registry/Tool internals must NOT be in the shim's __all__.

    They live in arc.runtime.subagents and may be refactored without
    notice. Out-of-tree packages should import only via arc.subagent_api.
    """
    import arc.subagent_api as api
    for forbidden in ("SubAgentRunner", "SubAgentRegistry", "SubAgentTool", "DispatchGuard"):
        assert forbidden not in api.__all__


def test_error_hierarchy():
    from arc.subagent_api import (
        SubAgentError,
        SubAgentRecursionError,
        SubAgentTimeoutError,
    )
    assert issubclass(SubAgentTimeoutError, SubAgentError)
    assert issubclass(SubAgentRecursionError, SubAgentError)

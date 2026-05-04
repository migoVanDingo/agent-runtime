"""Tests for the Container / DI pattern."""
import pytest
from runtime.container import Container


def test_container_build_succeeds():
    """Container.build() should succeed without raising."""
    container = Container.build()
    assert container.provider is not None
    assert container.registry is not None
    assert container.router is not None
    assert container.event_bus is not None


def test_container_has_toolsets():
    container = Container.build()
    # Registry should have at least the file_io toolset registered
    assert "file_io" in container.registry.toolset_names()


def test_two_containers_are_independent():
    """Two Container instances should not share registry state."""
    c1 = Container.build()
    c2 = Container.build()
    # They should be distinct objects
    assert c1.registry is not c2.registry
    assert c1.provider is not c2.provider

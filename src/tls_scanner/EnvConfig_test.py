from __future__ import annotations

from dataclasses import dataclass, field
import tls_scanner.EnvConfig

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_config(monkeypatch: pytest.MonkeyPatch):

    @dataclass(slots=True, frozen=True, kw_only=True)
    class Config(tls_scanner.EnvConfig.EnvConfig):
        a: str
        b: str = ""
        c: str | None = None

    monkeypatch.setenv("A", "a")
    monkeypatch.setenv("B", "b")
    monkeypatch.setenv("C", "c")

    config = Config.from_env()

    assert config.a == "a"
    assert config.b == "b"
    assert config.c == "c"

def test_config_computed_field():
    @dataclass(slots=True, frozen=True, kw_only=True)
    class Config(tls_scanner.EnvConfig.EnvConfig):
        computed: str = field(init=False, default="ready")

    config = Config.from_env()
    assert config.computed == "ready"

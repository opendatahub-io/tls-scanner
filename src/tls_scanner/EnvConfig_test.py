from __future__ import annotations

from dataclasses import dataclass, field
import tls_scanner.EnvConfig


def test_config():

    @dataclass(slots=True, frozen=True, kw_only=True)
    class Config(tls_scanner.EnvConfig.EnvConfig):
        a: str
        b: str = ""
        c: str | None = None

    config = Config.from_env(env={"A": "a", "B": "b", "C": "c"})

    assert config.a == "a"
    assert config.b == "b"
    assert config.c == "c"

def test_config_computed_field():
    @dataclass(slots=True, frozen=True, kw_only=True)
    class Config(tls_scanner.EnvConfig.EnvConfig):
        computed: str = field(init=False, default="ready")

    config = Config.from_env()
    assert config.computed == "ready"

def test_config_unset_optional_field():
    @dataclass(slots=True, frozen=True, kw_only=True)
    class Config(tls_scanner.EnvConfig.EnvConfig):
        optional: str | None

    config = Config.from_env()
    assert config.optional is None

def test_inherited_optional_field_without_default():
    @dataclass(frozen=True)
    class BaseConfig(tls_scanner.EnvConfig.EnvConfig):
        base_opt: str | None


    @dataclass(frozen=True)
    class ChildConfig(BaseConfig):
        child_req: str

    env = {"CHILD_REQ": "configured"}
    cfg = ChildConfig.from_env(env=env)

    assert cfg.child_req == "configured"
    assert cfg.base_opt is None

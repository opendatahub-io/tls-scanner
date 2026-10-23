from __future__ import annotations

import annotationlib
from dataclasses import MISSING, dataclass, fields
import os
import types
from typing import Self, get_args, get_origin, Union

@dataclass(frozen=True)
class EnvConfig:
    """Base dataclass that auto-populates fields from environment variables."""

    @classmethod
    def from_env(cls, env: dict[str, str | None] = os.environ) -> Self:
        annotations = annotationlib.get_annotations(cls, eval_str=True)
        kwargs: dict[str, str | None] = {}

        for f in fields(cls):
            if not f.init:
                continue

            env_name = f.metadata.get("env", f.name.upper())
            val = env.get(env_name)

            has_default = (f.default is not MISSING) or (f.default_factory is not MISSING)
            hint = annotations.get(f.name)
            is_optional = (
                get_origin(hint) in (Union, types.UnionType)
                and type(None) in get_args(hint)
            )

            if val is not None:
                kwargs[f.name] = val
            elif has_default:
                kwargs[f.name] = f.default if f.default is not MISSING else f.default_factory()
            elif is_optional:
                kwargs[f.name] = None
            else:
                raise ValueError(
                    f"Missing mandatory environment variable: '{env_name}' (field: '{f.name}')"
                )

        return cls(**kwargs)

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tls_scanner.EnvConfig

@dataclass(slots=True, frozen=True, kw_only=True)
class Config(tls_scanner.EnvConfig.EnvConfig):
    ref: str
    registry: str
    repository: str

    github_event_name: Literal["", "push", "pull-request"] = ""
    input_push: str = "false"
    github_output: str | None = None


def main() -> None:
    try:
        cfg: Config = Config.from_env()
    except ValueError as err:
        raise SystemExit(f"Config error: {err}")

    # Validate REF
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", cfg.ref):
        raise SystemExit(f"Invalid tls-scanner ref: '{cfg.ref}'")

    for name, value in (("registry", cfg.registry), ("repository", cfg.repository)):
        if re.search(r"[\r\n]", value):
            raise SystemExit(f"Invalid {name}: control characters are not allowed")

    image = f"{cfg.registry}/{cfg.repository.lower()}"

    proc = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    short_sha = proc.stdout.strip()

    tags = [f"{image}:{short_sha}"]
    if cfg.ref == "main":
        tags.append(f"{image}:latest")
    tags_str = ",".join(tags)

    push = "true" if cfg.github_event_name == "push" or cfg.input_push == "true" else "false"

    # Append output via pathlib.Path
    if cfg.github_output:
        Path(cfg.github_output).open("a", encoding="utf-8").write(
            f"image={image}\n"
            f"short_sha={short_sha}\n"
            f"tags={tags_str}\n"
            f"push={push}\n"
        )

    print(f"Image: {image}")
    print(f"Tags: {tags_str}")
    print(f"Push: {push}")


if __name__ == "__main__":
    main()

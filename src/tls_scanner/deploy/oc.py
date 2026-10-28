"""Thin subprocess wrappers around the `oc` CLI."""

from __future__ import annotations

import subprocess


def log(msg: str) -> None:
    print(msg, flush=True)


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run argv without a shell so nothing is word-split or expanded."""
    res = subprocess.run(cmd, capture_output=capture, text=True, check=False)
    if check and res.returncode != 0:
        raise RuntimeError(
            f"command failed ({res.returncode}): {' '.join(cmd)}\n"
            f"{res.stdout or ''}{res.stderr or ''}"
        )
    return res


def oc(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["oc", *args], check=check, capture=capture)


def oc_apply(manifest: str, what: str) -> None:
    res = subprocess.run(
        ["oc", "apply", "-f", "-"],
        input=manifest,
        text=True,
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"applying {what} failed:\n{res.stderr}")

"""Unit tests for tls_scanner.deploy.job."""

from __future__ import annotations

from .job import FINISHED_RE


def test_finished_regex() -> None:
    match = FINISHED_RE.search("Scanner finished with exit code: 0\n")
    assert match is not None and int(match.group(1)) == 0

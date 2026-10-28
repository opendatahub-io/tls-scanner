"""Unit tests for tls_scanner.deploy.cli."""

from __future__ import annotations

from .cli import (
    EXIT_CONFIG,
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_UNTRUSTED,
    _invalid_namespace_entries,
    build_parser,
)
from .manifests import DEFAULT_NAMESPACES


def test_default_arguments() -> None:
    args = build_parser().parse_args(["--image", "ghcr.io/example/tls-scanner@sha256:deadbeef"])
    assert args.namespace == "tls-scanner"
    assert args.namespaces == DEFAULT_NAMESPACES
    assert args.parallel == 16
    assert args.artifact_wait == 120
    assert args.timeout == 7200
    assert args.pqc_check is False
    assert args.keep is False


def test_namespace_name_validation() -> None:
    # --namespaces is embedded in the privileged pod's shell script, so each
    # entry is validated to a namespace-name shape: the legitimate default list
    # and common names pass, while shell metacharacters are rejected.
    assert _invalid_namespace_entries(DEFAULT_NAMESPACES) == []
    assert _invalid_namespace_entries("a-b,c2,") == []
    assert _invalid_namespace_entries("good,evil'quote") == ["evil'quote"]
    assert _invalid_namespace_entries("a;b|c") == ["a;b|c"]


def test_exit_codes() -> None:
    assert (EXIT_PASS, EXIT_FAIL, EXIT_CONFIG, EXIT_UNTRUSTED) == (0, 1, 2, 3)

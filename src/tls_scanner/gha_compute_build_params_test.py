from __future__ import annotations

import contextlib

import subprocess

import tls_scanner.gha_compute_build_params

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib
    import pytest


def test_build_params(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    tls_scanner_checkout = tmp_path / "tls_scanner/"
    tls_scanner_checkout.mkdir()
    subprocess.run("git init", shell=True, cwd=tls_scanner_checkout)
    subprocess.run("git commit --allow-empty --message 'initial commit'", shell=True, cwd=tls_scanner_checkout)
    tls_scanner_sha = subprocess.run("git rev-parse HEAD", shell=True, cwd=tls_scanner_checkout, capture_output=True, text=True).stdout.strip()
    tls_scanner_short_sha = tls_scanner_sha[:12]

    github_output = tmp_path / "github_output.txt"

    monkeypatch.setenv("REF", "c2989d368dcc98f25e5b953e0c5de18d38bf6e20")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("REGISTRY", "quay.io")
    monkeypatch.setenv("REPOSITORY", "repo")
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output.absolute()))

    with contextlib.chdir(tls_scanner_checkout):
        tls_scanner.gha_compute_build_params.main()

    assert github_output.exists()
    assert github_output.read_text() == "\n".join(
        (
            "image=quay.io/repo",
            f"short_sha={tls_scanner_short_sha}",
            f"tags=quay.io/repo:{tls_scanner_short_sha}",
            "push=true",
            "",
        )
    )

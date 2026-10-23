#!/usr/bin/env python3
"""Image acceptance tests for the tls-scanner container image.

Verifies that an image built from https://github.com/openshift/tls-scanner
(Dockerfile.local) works:
  1. CLI/tools smoke test (version checks, installed utilities)
  2. --dry-run smoke test
  3. Functional scan of a local TLS server (openssl s_server in a container)
  4. Positive compliance test: --expected-groups X25519 passes
  5. Negative compliance test: --pqc-check fails on a non-ML-KEM server (exit 1)

Runs without a Kubernetes cluster. Uses standard Python libraries only.
Usage:
    python3 ci/tls_scanner_image_test.py <image-ref>
Override engine via CONTAINER_ENGINE=podman (defaults to docker).
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def run_cmd(
    cmd: list[str],
    *,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, check=check, capture_output=capture_output, text=text)
    except subprocess.CalledProcessError as err:
        stderr_msg = err.stderr.strip() if err.stderr else ""
        stdout_msg = err.stdout.strip() if err.stdout else ""
        print(f"Command failed: {' '.join(cmd)} (exit {err.returncode})", file=sys.stderr)
        if stdout_msg:
            print(f"stdout: {stdout_msg}", file=sys.stderr)
        if stderr_msg:
            print(f"stderr: {stderr_msg}", file=sys.stderr)
        raise


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(f"usage: {sys.argv[0]} <image-ref>", file=sys.stderr)
        sys.exit(2)

    image_ref = sys.argv[1].strip()
    engine = os.environ.get("CONTAINER_ENGINE", "docker")
    port = 9443
    pid = os.getpid()
    network = f"tls-scanner-test-{pid}"
    server = f"tls-server-{pid}"
    artifacts_dir = tempfile.mkdtemp(prefix="tls-scanner-artifacts-")

    def cleanup() -> None:
        run_cmd([engine, "rm", "-f", server], check=False)
        run_cmd([engine, "network", "rm", network], check=False)
        shutil.rmtree(artifacts_dir, ignore_errors=True)

    try:
        print(f">>> [1/5] CLI and tools smoke test ({image_ref})")
        smoke_cmd = (
            "set -e\n"
            "tls-scanner --version\n"
            "testssl.sh --version 2>&1 | head -3\n"
            "oc version --client\n"
            "for tool in openssl jq bash wget socat tar; do\n"
            '  command -v "$tool"\n'
            "done\n"
        )
        run_cmd([engine, "run", "--rm", "--entrypoint", "bash", image_ref, "-c", smoke_cmd])
        print("PASS: [1/5] CLI and tools smoke test")

        print(">>> [2/5] --dry-run smoke test")
        run_cmd([engine, "run", "--rm", image_ref, "--host", "127.0.0.1", "--port", "443", "--dry-run"])
        print("PASS: [2/5] --dry-run smoke test")

        print(">>> [3/5] functional scan of a local TLS server")
        run_cmd([engine, "network", "create", network])

        server_cmd = (
            f"openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj '/CN={server}' "
            "-keyout /tmp/tls.key -out /tmp/tls.crt && "
            f"exec openssl s_server -accept {port} -key /tmp/tls.key -cert /tmp/tls.crt -www"
        )
        run_cmd([
            engine, "run", "--detach", "--name", server, "--network", network,
            "--entrypoint", "bash", image_ref, "-c", server_cmd,
        ])

        ready = False
        probe_cmd = f"exec 3<>/dev/tcp/127.0.0.1/{port}"
        for _ in range(30):
            res = run_cmd([engine, "exec", server, "bash", "-c", probe_cmd], check=False)
            if res.returncode == 0:
                ready = True
                break
            time.sleep(1)

        if not ready:
            fail("TLS test server did not become ready within 30 seconds")

        scan_cmd = [
            engine, "run", "--rm",
            "--network", network,
            "--volume", f"{artifacts_dir}:/results",
            image_ref,
            "--host", server,
            "--port", str(port),
            "--artifact-dir", "/results",
            "--json-file", "results.json",
            "--csv-file", "results.csv",
        ]
        run_cmd(scan_cmd)

        json_path = os.path.join(artifacts_dir, "results.json")
        csv_path = os.path.join(artifacts_dir, "results.csv")

        if not os.path.isfile(json_path) or os.path.getsize(json_path) == 0:
            fail(f"Scan JSON results missing or empty at {json_path}")
        if not os.path.isfile(csv_path) or os.path.getsize(csv_path) == 0:
            fail(f"Scan CSV results missing or empty at {csv_path}")

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        ip_results = data.get("ip_results", [])
        if len(ip_results) != 1:
            fail(f"Expected exactly 1 ip_result, got {len(ip_results)}")

        ip_res = ip_results[0]
        if ip_res.get("status") != "scanned":
            fail(f"Expected ip_results[0].status == 'scanned', got {ip_res.get('status')}")

        port_results = ip_res.get("port_results", [])
        if len(port_results) != 1:
            fail(f"Expected exactly 1 port_result, got {len(port_results)}")

        p_res = port_results[0]
        if p_res.get("port") != port:
            fail(f"Expected port_result.port == {port}, got {p_res.get('port')}")
        if p_res.get("status") != "OK":
            fail(f"Expected port_result.status == 'OK', got {p_res.get('status')}")
        if not p_res.get("tls13_supported"):
            fail("Expected tls13_supported == True")

        tls_versions = p_res.get("tls_versions") or []
        if "TLSv1.3" not in tls_versions:
            fail(f"Expected 'TLSv1.3' in tls_versions, got {tls_versions}")

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
            if len(rows) < 2:
                fail(f"CSV file has fewer than 2 rows (header + data): {rows}")

        print("PASS: [3/5] functional scan and result assertions")

        print(">>> [4/5] --expected-groups X25519 must pass (server offers X25519)")
        run_cmd([
            engine, "run", "--rm",
            "--network", network,
            image_ref,
            "--host", server,
            "--port", str(port),
            "--expected-groups", "X25519",
            "--expected-groups-mode", "contains",
        ])
        print("PASS: [4/5] --expected-groups X25519 passed")

        print(">>> [5/5] --pqc-check must fail with exit code 1 (server has no ML-KEM)")
        res = run_cmd(
            [engine, "run", "--rm", "--network", network, image_ref, "--host", server, "--port", str(port), "--pqc-check"],
            check=False,
        )
        if res.returncode != 1:
            fail(f"Expected --pqc-check exit code 1, got {res.returncode}")
        print("PASS: [5/5] --pqc-check failed as expected with exit code 1")

        print(f"\nPASS: all acceptance tests passed for {image_ref}")
    finally:
        cleanup()


if __name__ == "__main__":
    main()

"""Scanner Job lifecycle: pod watch, completion marker polling, and artifact collection."""

from __future__ import annotations

import json
import os
import re
import time

from .oc import log, oc

FINISHED_RE = re.compile(r"Scanner finished with exit code:\s*(-?\d+)")
PQC_FAILED = "PQC COMPLIANCE CHECK: FAILED"
PQC_PASSED = "PQC COMPLIANCE CHECK: PASSED"

REQUIRED_ARTIFACTS = ("results.json", "results.csv", "junit_tls_scan.xml")
OPTIONAL_ARTIFACTS = ("scan.log", "stdout.log", "timing.txt")


def wait_for_pod(namespace: str, job_name: str, timeout: int) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = oc(
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            f"job-name={job_name}",
            "-o",
            "jsonpath={.items[0].metadata.name}",
            check=False,
        )
        name = (res.stdout or "").strip()
        if name:
            return name
        time.sleep(5)
    raise RuntimeError(f"no pod appeared for job {job_name} within {timeout}s")


def wait_for_finish(namespace: str, pod: str, timeout: int) -> tuple[int, str]:
    """Poll the log for the scanner's own exit code.

    The Job always exits 0, so the pod phase cannot tell us whether the
    scan passed; the manifest always prints the marker line.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = oc("logs", pod, "-n", namespace, check=False).stdout or ""
        match = FINISHED_RE.search(text)
        if match:
            return int(match.group(1)), text
        phase = (
            oc(
                "get",
                "pod",
                pod,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.phase}",
                check=False,
            ).stdout
            or ""
        ).strip()
        if phase in ("Failed", "Error"):
            raise RuntimeError(f"pod {pod} failed before finishing (phase {phase})")
        time.sleep(5)
    raise RuntimeError(f"scanner did not finish within {timeout}s")


def collect(namespace: str, pod: str, out_dir: str) -> None:
    """Copy the scanner's artifacts out of the pod.

    results.json, results.csv and junit_tls_scan.xml are the ones the gate
    and Jenkins publishing depend on; scan.log and timing.txt are
    diagnostics that the scanner may legitimately not write. Existence is
    checked rather than relying on the cp exit status, which can succeed
    while producing no file.
    """
    os.makedirs(out_dir, exist_ok=True)
    missing: list[str] = []
    for name in REQUIRED_ARTIFACTS + OPTIONAL_ARTIFACTS:
        dest = os.path.join(out_dir, name)
        if os.path.exists(dest):
            os.remove(dest)
        res = oc("cp", f"{namespace}/{pod}:/artifacts/{name}", dest, check=False)
        if res.returncode != 0 or not os.path.isfile(dest):
            if name in REQUIRED_ARTIFACTS:
                missing.append(name)
            else:
                log(f"WARNING: {name} not produced by the scanner")
            continue
        log(f"Collected {name} ({os.path.getsize(dest)} bytes)")
    if missing:
        raise RuntimeError(f"scanner did not produce required artifact(s): {', '.join(missing)}")


def summarise(results_path: str) -> None:
    try:
        with open(results_path) as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        log(f"Could not parse results.json: {exc}")
        return
    log(f"Scanned {len(data.get('ip_results') or [])} IP(s)")

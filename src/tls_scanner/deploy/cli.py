#!/usr/bin/env python3
"""Deploy tls-scanner as an in-cluster Job and gate on its result.

Reimplementation of the deploy/collect/cleanup flow in
openshift/tls-scanner deploy.sh, with the differences this pipeline needs:

  * The scanner runs in a namespace owned by this run, so teardown is
    "oc delete namespace" instead of stripping RBAC off a shared SA.
  * --junit-file is requested so results feed Jenkins' junit step.
  * The Job exits 0 on purpose (results are artifacts, matching upstream),
    so the gate reads the scanner's own exit code from the pod log rather
    than trusting the Job status.

Remaining gap: a silently-unreachable endpoint still looks like a clean
one. Detecting "the scanner could not connect at all" and failing the run
on it is not implemented here.

Exit codes: 0 pass, 1 compliance failure, 2 configuration error,
3 scan did not produce a trustworthy result.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from .cluster import set_up, teardown
from .job import PQC_FAILED, PQC_PASSED, collect, summarise, wait_for_finish, wait_for_pod
from .manifests import DEFAULT_NAMESPACES, NETWORK_POLICY_SELECTOR, render_job_manifest
from .oc import log, oc, oc_apply

EXIT_PASS, EXIT_FAIL, EXIT_CONFIG, EXIT_UNTRUSTED = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tls-scanner-deploy",
        description="Deploy tls-scanner as an in-cluster Job and gate on its result.",
    )
    parser.add_argument(
        "--image",
        required=True,
        help="pinned tls-scanner image, ideally by digest",
    )
    parser.add_argument("--namespace", default="tls-scanner")
    parser.add_argument("--namespaces", default=DEFAULT_NAMESPACES)
    parser.add_argument("--job-name", default="tls-scanner-job")
    parser.add_argument("--cluster-role", default="tls-scanner-cross-namespace")
    parser.add_argument("--parallel", default="16")
    parser.add_argument("--artifact-wait", default="120")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--out-dir", default="tls-scan-results")
    parser.add_argument(
        "--pqc-check",
        action="store_true",
        help=(
            "narrow the scan to a TLS 1.3 + ML-KEM (post-quantum) probe; "
            "without this the scanner performs the full TLS scan with policy compliance"
        ),
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the namespace and RBAC in place",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if not shutil.which("oc"):
        log("ERROR: oc is not on PATH")
        return EXIT_CONFIG

    manifest = render_job_manifest(
        job_name=args.job_name,
        namespace=args.namespace,
        image=args.image,
        parallel=args.parallel,
        namespaces=args.namespaces,
        pqc_check=args.pqc_check,
        artifact_wait=args.artifact_wait,
    )

    target_namespaces = [ns.strip() for ns in args.namespaces.split(",") if ns.strip()]

    scanner_rc: int | None = None
    verdict_missing = False
    policies_applied = 0
    try:
        policies_applied = set_up(args.namespace, args.cluster_role, target_namespaces)
        oc_apply(manifest, "Job")

        pod = wait_for_pod(args.namespace, args.job_name, args.timeout)
        log(f"Scanner pod: {pod}")

        scanner_rc, _ = wait_for_finish(args.namespace, pod, args.timeout)
        log(f"Scanner reported exit code {scanner_rc}")

        collect(args.namespace, pod, args.out_dir)
        summarise(os.path.join(args.out_dir, "results.json"))

        # Print the pod log for the console. wait_for_finish() only returns
        # once the marker is present, so this window already contains the
        # scanner's verdict and any warnings.
        logs = oc("logs", pod, "-n", args.namespace, check=False).stdout or ""
        print(logs)

        # The verdict is reported from stdout, which the manifest captures
        # into stdout.log; fall back to the collected copy when the console
        # stream was already closed.
        verdict_text = logs
        stdout_copy = os.path.join(args.out_dir, "stdout.log")
        if PQC_FAILED not in verdict_text and PQC_PASSED not in verdict_text:
            try:
                with open(stdout_copy, errors="replace") as handle:
                    verdict_text += handle.read()
            except OSError:
                pass

        if PQC_FAILED in verdict_text:
            log("PQC compliance check: FAILED")
        elif PQC_PASSED in verdict_text:
            log("PQC compliance check: PASSED")
        elif args.pqc_check:
            # --pqc-check always prints one of the two markers, so absence
            # means the scan did not run to completion. Record it and let
            # the normal exit handling below report it.
            log(
                "ERROR: --pqc-check was requested but no PQC verdict was "
                "reported; treating the result as untrustworthy"
            )
            verdict_missing = True
    except Exception as exc:  # noqa: BLE001 - surfaced into the build log
        log(f"ERROR: {exc}")
        return EXIT_UNTRUSTED
    finally:
        if args.keep:
            log(
                f"--keep: leaving namespace {args.namespace} and "
                f"{policies_applied} NetworkPolicy(ies) in place"
            )
            if policies_applied:
                log(
                    "--keep: to clean them up later, run: "
                    f"oc delete networkpolicy --all-namespaces -l {NETWORK_POLICY_SELECTOR}"
                )
        else:
            teardown(args.namespace, args.cluster_role, policies_applied)

    if scanner_rc is None or verdict_missing:
        return EXIT_UNTRUSTED
    if scanner_rc != 0:
        log(f"TLS scan failed (scanner exit {scanner_rc})")
        return EXIT_FAIL
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())

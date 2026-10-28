"""Unit tests for tls_scanner.deploy.manifests."""

from __future__ import annotations

import yaml

from .manifests import (
    CLUSTER_ROLE_MANIFEST,
    DEFAULT_NAMESPACES,
    NETWORK_POLICY_LABEL_KEY,
    NETWORK_POLICY_LABEL_VALUE,
    NETWORK_POLICY_SELECTOR,
    SCANNER_EGRESS_POLICY_MANIFEST,
    TARGET_INGRESS_POLICY_MANIFEST,
    render_job_manifest,
)


def test_render_job_manifest_tracks_parallel_and_caps_cpu() -> None:
    raw = render_job_manifest(
        job_name="tls-scanner-job",
        namespace="tls-scanner",
        image="ghcr.io/opendatahub-io/tls-scanner@sha256:abc",
        parallel=16,
        namespaces=DEFAULT_NAMESPACES,
        pqc_check=True,
        artifact_wait=120,
    )
    doc = yaml.safe_load(raw)
    assert doc["kind"] == "Job"
    container = doc["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"]["limits"]["cpu"] == "16"
    script = container["args"][0]
    assert "--all-pods" in script
    assert "-j 16" in script
    assert "--pqc-check" in script
    assert "--timing-file timing.txt" in script

    capped = yaml.safe_load(
        render_job_manifest(
            job_name="tls-scanner-job",
            namespace="tls-scanner",
            image="ghcr.io/opendatahub-io/tls-scanner@sha256:abc",
            parallel=64,
            namespaces=DEFAULT_NAMESPACES,
            pqc_check=False,
            artifact_wait=120,
        )
    )
    assert capped["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["cpu"] == "32"


def test_network_policy_and_cluster_role_manifests_parse_as_yaml() -> None:
    egress = yaml.safe_load(
        SCANNER_EGRESS_POLICY_MANIFEST.format(
            scanner_namespace="tls-scanner",
            label_key=NETWORK_POLICY_LABEL_KEY,
            label_value=NETWORK_POLICY_LABEL_VALUE,
        )
    )
    assert egress["metadata"]["labels"] == {NETWORK_POLICY_LABEL_KEY: NETWORK_POLICY_LABEL_VALUE}
    assert NETWORK_POLICY_SELECTOR == "tls-scanner/cleanup=dangling-policy"

    ingress = yaml.safe_load(
        TARGET_INGRESS_POLICY_MANIFEST.format(
            target_namespace="redhat-ods-applications",
            scanner_namespace="tls-scanner",
            label_key=NETWORK_POLICY_LABEL_KEY,
            label_value=NETWORK_POLICY_LABEL_VALUE,
        )
    )
    ns_sel = ingress["spec"]["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"]
    assert ns_sel == {"kubernetes.io/metadata.name": "tls-scanner"}

    role = yaml.safe_load(CLUSTER_ROLE_MANIFEST.format(cluster_role="tls-scanner-cross-namespace"))
    assert role["kind"] == "ClusterRole"

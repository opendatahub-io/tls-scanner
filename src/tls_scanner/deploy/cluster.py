"""Cluster setup and teardown: namespace, RBAC, SCC, and NetworkPolicies.

Default-deny network policy support:

  A cluster enforcing default-deny ingress and egress would leave the
  scanner unable to reach any target, and unreachable endpoints must never
  be mistaken for compliant ones: a restrictive cluster has to remain
  distinguishable from a healthy one.

  The scan therefore creates two kinds of NetworkPolicy:

    * egress in the scanner namespace, so probes can leave the pod, and
    * ingress in EACH scanned namespace, so the target's own default-deny
      does not drop them.

  NetworkPolicy is namespaced and additive, so a policy in the scanner's
  namespace cannot grant ingress into another namespace. The second kind
  necessarily mutates namespaces we do not own; that is accepted as the
  cost of scanning there at all.

  Every policy carries tls-scanner/cleanup=dangling-policy, so teardown is
  one cluster-wide command:

      oc delete networkpolicy --all-namespaces \
          -l tls-scanner/cleanup=dangling-policy

  --all-namespaces is what makes it one command: a bare label selector only
  matches the current project, which is whatever ocLoginToCluster left
  active, not necessarily a scanned one. It also sweeps policies left behind
  by an aborted run. The key is deliberately specific because the blast
  radius of that delete is the whole cluster: anything not carrying this
  exact label must never be deleted, and nothing else in the repository may
  use the key.

  Note the asymmetry: the egress policy lives in the scanner's own namespace
  and dies with it, so its label matters mainly under --keep or after an
  abort. The ingress policies in the scanned namespaces are the ones that
  genuinely require label-based deletion, because those namespaces outlive
  the run.
"""

from __future__ import annotations

from collections.abc import Iterable

from .manifests import (
    CLUSTER_ROLE_MANIFEST,
    NETWORK_POLICY_LABEL_KEY,
    NETWORK_POLICY_LABEL_VALUE,
    NETWORK_POLICY_SELECTOR,
    SCANNER_EGRESS_POLICY_MANIFEST,
    TARGET_INGRESS_POLICY_MANIFEST,
)
from .oc import log, oc, oc_apply


def apply_network_policies(scanner_namespace: str, target_namespaces: Iterable[str]) -> int:
    """Let the scan reach every target despite default-deny policies.

    Returns the number of policies applied, so teardown can tell an empty
    cleanup apart from one that should have found something.
    """
    applied = 0
    log(f"Applying egress NetworkPolicy in {scanner_namespace}")
    oc_apply(
        SCANNER_EGRESS_POLICY_MANIFEST.format(
            scanner_namespace=scanner_namespace,
            label_key=NETWORK_POLICY_LABEL_KEY,
            label_value=NETWORK_POLICY_LABEL_VALUE,
        ),
        "scanner egress NetworkPolicy",
    )
    applied += 1

    # A cluster carries one of the ODH/RHOAI namespace pairs, not both, so
    # the configured filter routinely names namespaces that do not exist.
    # Applying a policy into a missing namespace would fail the whole run.
    existing = set(
        (
            oc(
                "get",
                "namespaces",
                "-o",
                "jsonpath={.items[*].metadata.name}",
                check=False,
            ).stdout
            or ""
        ).split()
    )
    for target_ns in target_namespaces:
        if target_ns not in existing:
            log(f"Skipping ingress NetworkPolicy for absent namespace {target_ns}")
            continue
        log(f"Applying ingress NetworkPolicy in {target_ns}")
        oc_apply(
            TARGET_INGRESS_POLICY_MANIFEST.format(
                target_namespace=target_ns,
                scanner_namespace=scanner_namespace,
                label_key=NETWORK_POLICY_LABEL_KEY,
                label_value=NETWORK_POLICY_LABEL_VALUE,
            ),
            f"ingress NetworkPolicy in {target_ns}",
        )
        applied += 1

    log(f"Applied {applied} NetworkPolicy(ies) labelled {NETWORK_POLICY_SELECTOR}")
    return applied


def set_up(namespace: str, cluster_role: str, target_namespaces: Iterable[str]) -> int:
    log(f"Creating namespace {namespace}")
    oc("create", "namespace", namespace, check=False)

    # The scanner needs a privileged pod and cross-namespace reads. Both are
    # granted to this namespace's default SA, which exists only for this run
    # and is destroyed with the namespace.
    log("Granting privileged SCC and cluster-reader to the default SA")
    oc("adm", "policy", "add-scc-to-user", "privileged", "-z", "default", "-n", namespace)
    oc("adm", "policy", "add-cluster-role-to-user", "cluster-reader", "-z", "default", "-n", namespace)

    log(f"Creating ClusterRole {cluster_role}")
    oc_apply(CLUSTER_ROLE_MANIFEST.format(cluster_role=cluster_role), "ClusterRole")
    oc("adm", "policy", "add-cluster-role-to-user", cluster_role, "-z", "default", "-n", namespace)

    return apply_network_policies(namespace, target_namespaces)


def remove_network_policies() -> int:
    """Delete every policy carrying our label, cluster-wide.

    The selector spans all namespaces on purpose: the ingress policies live
    in namespaces we do not own and outlive this run. Counted before and
    after because "oc delete -l" exits 0 and prints "No resources found"
    when nothing matches, making a silent no-op look like success.
    """

    def count() -> int:
        res = oc(
            "get",
            "networkpolicy",
            "--all-namespaces",
            "-l",
            NETWORK_POLICY_SELECTOR,
            "-o",
            "name",
            check=False,
        )
        return len([line for line in (res.stdout or "").splitlines() if line.strip()])

    before = count()
    if before == 0:
        log(f"No NetworkPolicy labelled {NETWORK_POLICY_SELECTOR} to remove")
        return 0
    oc("delete", "networkpolicy", "--all-namespaces", "-l", NETWORK_POLICY_SELECTOR, check=False)
    remaining = count()
    removed = before - remaining
    log(f"Removed {removed} NetworkPolicy(ies) labelled {NETWORK_POLICY_SELECTOR}")
    if remaining:
        log(f"WARNING: {remaining} NetworkPolicy(ies) still present after delete")
    return removed


def teardown(namespace: str, cluster_role: str, policies_applied: int) -> None:
    log("Cleaning up")
    removed = remove_network_policies()
    if policies_applied and not removed:
        log(
            f"WARNING: {policies_applied} NetworkPolicy(ies) were applied this run "
            "but none were found for cleanup"
        )
    # The privileged SCC and the cluster roles were granted to this
    # namespace's default SA through cluster-scoped objects
    # (add-scc-to-user / add-cluster-role-to-user). Namespace deletion does
    # not remove those grants, so a later namespace with the same name would
    # otherwise inherit its default SA's stale grants. Revoke each before the
    # namespace goes away.
    oc("adm", "policy", "remove-scc-from-user", "privileged", "-z", "default", "-n", namespace, check=False)
    oc("adm", "policy", "remove-cluster-role-from-user", "cluster-reader", "-z", "default", "-n", namespace, check=False)
    oc("adm", "policy", "remove-cluster-role-from-user", cluster_role, "-z", "default", "-n", namespace, check=False)
    # The ClusterRole outlives the namespace, so remove it explicitly.
    oc("delete", "clusterrole", cluster_role, "--ignore-not-found=true", check=False)
    # Deleting the namespace removes the namespaced Job, pod and its emptyDir.
    oc("delete", "namespace", namespace, "--ignore-not-found=true", "--wait=false", check=False)

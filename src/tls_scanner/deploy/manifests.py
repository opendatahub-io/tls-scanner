"""Kubernetes and OpenShift manifests for deploying the in-cluster TLS scanner."""

from __future__ import annotations

DEFAULT_NAMESPACES = ",".join([
    "opendatahub-operator",
    "opendatahub",
    "redhat-ods-operator",
    "redhat-ods-applications",
])

# NetworkPolicies for the scan.
#
# Two kinds are needed, not one: egress in the scanner namespace so probes
# can leave the pod, and ingress in EACH scanned namespace so the target's
# own default-deny does not drop them. NetworkPolicy is namespaced and
# additive, so the second kind necessarily touches namespaces we do not own.
#
# Every policy carries NETWORK_POLICY_SELECTOR. Teardown deletes cluster-wide
# by that selector -- the only single-command cleanup, and it also sweeps
# policies left behind by an aborted run. The key is deliberately specific
# because the blast radius of that delete is the whole cluster.
NETWORK_POLICY_LABEL_KEY = "tls-scanner/cleanup"
NETWORK_POLICY_LABEL_VALUE = "dangling-policy"
NETWORK_POLICY_SELECTOR = f"{NETWORK_POLICY_LABEL_KEY}={NETWORK_POLICY_LABEL_VALUE}"

JOB_MANIFEST = """\
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
spec:
  backoffLimit: 0
  template:
    spec:
      serviceAccountName: default
      priorityClassName: system-cluster-critical
      restartPolicy: Never
      containers:
      - name: tls-scanner
        image: {image}
        imagePullPolicy: Always
        command: ["/bin/sh", "-c"]
        args:
          - |
            # The scanner writes diagnostics to --log-file but its PQC verdict
            # goes to stdout; capture both so the tail below surfaces them and
            # both files get archived.
            /usr/local/bin/tls-scanner \\
              --all-pods \\
              -j {parallel} \\
              --artifact-dir /artifacts \\
              --json-file /artifacts/results.json \\
              --csv-file /artifacts/results.csv \\
              --junit-file /artifacts/junit_tls_scan.xml \\
              --timing-file timing.txt \\
              --log-file /artifacts/scan.log \\
              --namespace-filter '{namespaces}' \\
              {pqc_flag} >> /artifacts/stdout.log 2>&1 &
            SCANNER_PID=$!
            tail -f --retry /artifacts/scan.log /artifacts/stdout.log &
            TAIL_PID=$!
            wait $SCANNER_PID
            SCANNER_RC=$?
            kill $TAIL_PID 2>/dev/null || true
            sleep 2
            echo ""
            echo "=========================================="
            echo "Scanner finished with exit code: $SCANNER_RC"
            echo "=========================================="
            echo "Pausing for artifact collection..."
            sleep {artifact_wait}
            exit 0
        resources:
          requests:
            cpu: "{cpu_request}"
            memory: "{mem_request}"
          limits:
            cpu: "{cpu_limit}"
            memory: "{mem_limit}"
        securityContext:
          privileged: true
        volumeMounts:
        - name: artifacts
          mountPath: /artifacts
      volumes:
      - name: artifacts
        emptyDir: {{}}
"""

SCANNER_EGRESS_POLICY_MANIFEST = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tls-scanner-allow-egress
  namespace: {scanner_namespace}
  labels:
    {label_key}: "{label_value}"
spec:
  podSelector: {{}}
  policyTypes: ["Egress"]
  egress:
  - {{}}
"""

# kubernetes.io/metadata.name is set by the API server on every namespace, so
# the scanner namespace needs no label of its own for this selector to match.
TARGET_INGRESS_POLICY_MANIFEST = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tls-scanner-allow-ingress
  namespace: {target_namespace}
  labels:
    {label_key}: "{label_value}"
spec:
  podSelector: {{}}
  policyTypes: ["Ingress"]
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: {scanner_namespace}
"""

CLUSTER_ROLE_MANIFEST = """\
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {cluster_role}
rules:
- apiGroups: [""]
  resources: ["pods/exec"]
  verbs: ["create"]
- apiGroups: ["operator.openshift.io"]
  resources: ["ingresscontrollers"]
  verbs: ["get", "list"]
- apiGroups: ["machineconfiguration.openshift.io"]
  resources: ["kubeletconfigs"]
  verbs: ["get", "list"]
"""


def render_job_manifest(
    *,
    job_name: str,
    namespace: str,
    image: str,
    parallel: str | int,
    namespaces: str,
    pqc_check: bool,
    artifact_wait: str | int,
    cpu_request: str = "500m",
    cpu_limit: str | None = None,
    mem_request: str = "4Gi",
    mem_limit: str = "4Gi",
) -> str:
    """Render the in-cluster scanner Job manifest.

    One worker is one testssl.sh/openssl process, and openssl is CPU-bound
    while handshaking. A cpu limit below the worker count throttles the whole
    batch and can make it slower than fewer workers, so the default limit
    tracks `parallel` (capped at 32).
    """
    if cpu_limit is None:
        cpu_limit = str(min(int(parallel), 32))
    return JOB_MANIFEST.format(
        job_name=job_name,
        namespace=namespace,
        image=image,
        parallel=parallel,
        namespaces=namespaces,
        pqc_flag="--pqc-check" if pqc_check else "",
        artifact_wait=artifact_wait,
        cpu_request=cpu_request,
        cpu_limit=cpu_limit,
        mem_request=mem_request,
        mem_limit=mem_limit,
    )

"""
Guardrails for the Kafka infrastructure (see KAFKA.md stages 0-2).

These assert on Terraform and Helm sources rather than on Python, for the same
reason tests/test_architecture.py asserts on the chart's worker queues: the
failures they catch are silent at runtime. A broker whose StorageClass does not
exist does not error — it sits Pending forever with the real cause two levels
below anything that logs. A replication factor quietly dropped to 1 does not
error either; it simply stops being durable, and nothing says so until a broker
is lost.

Parsing the sources directly (no helm binary, no cluster) keeps this runnable in
CI, which is the only place it will run consistently.
"""

from __future__ import annotations

import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EKS_DIR = REPO_ROOT / "infra" / "eks"
KAFKA_CHART = REPO_ROOT / "infra" / "helm" / "kafka"
KAFKA_VALUES = KAFKA_CHART / "values.yaml"
KAFKA_TEMPLATE = KAFKA_CHART / "templates" / "kafka.yaml"
UI_TEMPLATE = KAFKA_CHART / "templates" / "kafka-ui.yaml"
UI_CONFIG_TEMPLATE = KAFKA_CHART / "templates" / "kafka-ui-config.yaml"

# Provisioners removed from Kubernetes. A StorageClass naming one of these looks
# perfectly healthy in `kubectl get sc` and provisions nothing, which is exactly
# how the cluster shipped with a `gp2` class that had never worked.
DEAD_IN_TREE_PROVISIONERS = {
    "kubernetes.io/aws-ebs",
    "kubernetes.io/gce-pd",
    "kubernetes.io/azure-disk",
    "kubernetes.io/cinder",
}


def _values() -> dict:
    return yaml.safe_load(KAFKA_VALUES.read_text())


def _terraform() -> str:
    """Every .tf file in the EKS stack, concatenated."""
    return "\n".join(p.read_text() for p in sorted(EKS_DIR.glob("*.tf")))


def _tf_default(variable: str) -> str:
    """The `default` of a Terraform variable block, as raw text."""
    body = re.search(
        rf'variable\s+"{variable}"\s*\{{(.*?)\n\}}',
        _terraform(),
        re.DOTALL,
    )
    assert body, f"No Terraform variable named {variable!r}."
    default = re.search(r"^\s*default\s*=\s*(.+)$", body.group(1), re.MULTILINE)
    assert default, f"Variable {variable!r} has no default."
    return default.group(1).strip()


class TestStorage:
    """Stage 0. The cluster could not provision a volume at all before this."""

    def test_terraform_declares_a_storage_class_the_kafka_chart_asks_for(self) -> None:
        """
        The chart names a StorageClass; Terraform creates one. Nothing ties the
        two together except this test.

        Get it wrong and there is no error anywhere: the PVC stays Pending, the
        broker pod stays Pending behind it, and the Kafka resource never goes
        Ready — with the actual cause (a class name that matches nothing)
        visible only in `kubectl describe pvc`.
        """
        wanted = _values()["storage"]["class"]
        declared = set(
            re.findall(
                r'resource\s+"kubernetes_storage_class_v1"[^{]*\{.*?name\s*=\s*"([^"]+)"',
                _terraform(),
                re.DOTALL,
            )
        )
        assert wanted in declared, (
            f"The Kafka chart requests StorageClass {wanted!r}, but the EKS stack "
            f"creates {sorted(declared)}. Every broker PVC would hang Pending."
        )

    def test_no_storage_class_uses_a_removed_in_tree_provisioner(self) -> None:
        """The gp2 trap: a class that looks fine and provisions nothing."""
        used = set(re.findall(r'storage_provisioner\s*=\s*"([^"]+)"', _terraform()))
        dead = used & DEAD_IN_TREE_PROVISIONERS
        assert not dead, (
            f"StorageClass(es) use provisioners removed from Kubernetes: {sorted(dead)}. "
            "PVCs bound to them stay Pending forever with nothing watching them."
        )

    def test_exactly_one_storage_class_is_marked_default(self) -> None:
        """
        Zero defaults means a PVC that names no class gets none at all — the
        binder reports "no storage class is set" and never reaches a
        provisioner. Two defaults is undefined behaviour.
        """
        defaults = re.findall(
            r'"storageclass\.kubernetes\.io/is-default-class"\s*=\s*"true"',
            _terraform(),
        )
        assert len(defaults) == 1, (
            f"Expected exactly one default StorageClass, found {len(defaults)}."
        )


class TestDurability:
    """
    Stage 2. From KAFKA.md: "anything less and the durability story is theatre."

    These numbers are the entire reason for running three brokers. Dropping
    min.insync.replicas to 1 makes every symptom go away — writes keep flowing
    during a broker failure — while silently removing the guarantee. That is
    precisely why it is asserted rather than trusted to review.
    """

    def test_replication_factor_is_three(self) -> None:
        assert _values()["durability"]["replicationFactor"] == 3

    def test_min_insync_replicas_is_two(self) -> None:
        assert _values()["durability"]["minInsyncReplicas"] == 2

    def test_min_insync_replicas_leaves_room_for_one_broker_to_fail(self) -> None:
        """
        The pairing is what matters, not either number alone. With RF == ISR a
        single broker going down stops writes entirely; with ISR == 1 there is
        no durability guarantee left. RF - ISR == 1 is the point that tolerates
        one failure without giving up the second copy.
        """
        d = _values()["durability"]
        assert d["replicationFactor"] - d["minInsyncReplicas"] == 1

    def test_internal_topics_are_replicated_too(self) -> None:
        """
        Kafka's internal topics do NOT inherit default.replication.factor —
        they are configured separately and default to 1. A cluster with
        perfectly replicated data and a single-replica __consumer_offsets loses
        every consumer position when the wrong broker dies.
        """
        template = KAFKA_TEMPLATE.read_text()
        for key in (
            "offsets.topic.replication.factor",
            "transaction.state.log.replication.factor",
        ):
            assert f"{key}: {{{{ .Values.durability.replicationFactor }}}}" in template, (
                f"{key} must be set from durability.replicationFactor, not left "
                "to Kafka's default of 1."
            )


class TestScheduling:
    """Stage 1 and 2 have to agree about how many nodes exist."""

    def test_brokers_fit_on_the_nodes_terraform_provisions(self) -> None:
        """
        The brokers spread hard across hostname, so more brokers than nodes
        means the surplus sits Pending indefinitely. This is the link between
        the chart's replica count and the node group's size.
        """
        replicas = _values()["nodePool"]["replicas"]
        nodes = int(_tf_default("node_desired_size"))
        assert replicas <= nodes, (
            f"{replicas} brokers spread hard across hostname cannot schedule on "
            f"{nodes} nodes — the extras stay Pending."
        )

    def test_broker_count_can_satisfy_the_replication_factor(self) -> None:
        d = _values()["durability"]
        assert _values()["nodePool"]["replicas"] >= d["replicationFactor"], (
            "Fewer brokers than the replication factor: topics can never reach "
            "their target replica count."
        )

    def test_the_node_spread_is_hard_and_the_zone_spread_is_soft(self) -> None:
        """
        Deliberately NOT "one broker per AZ" as KAFKA.md's prose suggests.

        Hard across hostname: two brokers on one node means one node failure
        drops two of three replicas and min.insync.replicas can no longer be
        met. Worth refusing to schedule over.

        Soft across zone: ap-south-1a had zero t4g.small capacity when this
        cluster was built — that is why node_instance_types is a list at all. A
        hard zone constraint turns a capacity shortfall into a broker that never
        schedules.
        """
        topology = _values()["topology"]
        assert topology["nodeSpread"] == "DoNotSchedule"
        assert topology["zoneSpread"] == "ScheduleAnyway"


class TestManifestShape:
    """Version traps that produce confusing failures rather than clear ones."""

    def test_kafka_manifests_use_the_v1_api(self) -> None:
        """
        Strimzi 1.x serves ONLY kafka.strimzi.io/v1 — v1beta2 was removed, not
        deprecated. Nearly every Strimzi example still in circulation is
        v1beta2, so a copy-paste from the internet is rejected by the API
        server with a message about an unknown kind.
        """
        versions = set(
            re.findall(
                r"^apiVersion:\s*(kafka\.strimzi\.io/\S+)", KAFKA_TEMPLATE.read_text(), re.MULTILINE
            )
        )
        assert versions == {"kafka.strimzi.io/v1"}, (
            f"Kafka manifests must use kafka.strimzi.io/v1; found {sorted(versions)}."
        )

    def test_the_template_actually_reads_the_values_the_tests_assert_on(self) -> None:
        """
        Everything above checks values.yaml. That is only meaningful if the
        template consumes those keys — a hardcoded literal in the template would
        make every assertion here vacuous.
        """
        template = KAFKA_TEMPLATE.read_text()
        for path in (
            ".Values.storage.class",
            ".Values.nodePool.replicas",
            ".Values.durability.replicationFactor",
            ".Values.durability.minInsyncReplicas",
            ".Values.topology.nodeSpread",
            ".Values.topology.zoneSpread",
        ):
            assert path in template, (
                f"{path} is asserted by these tests but never read by the "
                "template, so the assertion guarantees nothing."
            )

    def test_broker_heap_stays_below_the_memory_limit(self) -> None:
        """
        A JVM heap that can grow into the container limit gets the pod
        OOMKilled by the kernel instead of throwing OutOfMemoryError — no Java
        stack trace, just a restart loop. Kafka also wants the slack for page
        cache, which is where its read throughput comes from.
        """
        values = _values()
        heap_mib = int(values["jvm"]["xmx"].removesuffix("g")) * 1024
        limit_mib = int(values["resources"]["limits"]["memory"].removesuffix("Mi"))
        assert heap_mib < limit_mib, "Heap must be smaller than the memory limit."
        assert limit_mib - heap_mib >= 256, (
            f"Only {limit_mib - heap_mib} MiB between heap and limit — too little "
            "headroom for page cache and JVM overhead."
        )


class TestKafkaUI:
    """
    AKHQ, the Kafka console. It is an UNAUTHENTICATED admin interface, and the
    only thing keeping that acceptable is that it is unreachable from outside
    the cluster. Every assertion here defends that assumption, because breaking
    it produces no error — just a working, public console.
    """

    def test_the_service_is_cluster_ip(self) -> None:
        """
        LoadBalancer or NodePort would hand it a public address. Nothing warns
        about this; it simply starts working from the internet.
        """
        assert _values()["ui"]["service"]["type"] == "ClusterIP"

    def test_there_is_no_ingress_for_the_ui(self) -> None:
        """
        The chart must not grow an Ingress template. Behind the ALB this would
        publish read access to every event payload under a guessable hostname —
        and it would look like a convenience feature in review.
        """
        for template in KAFKA_CHART.glob("templates/*.yaml"):
            body = template.read_text()
            assert "kind: Ingress" not in body, (
                f"{template.name} declares an Ingress. The Kafka UI has no "
                "authentication; reach it with kubectl port-forward instead."
            )

    def test_the_ui_is_read_only(self) -> None:
        """
        Consistency, not just caution. Topics are KafkaTopic manifests
        reconciled by the Topic Operator. One created through the UI exists in
        Kafka with no KafkaTopic behind it — invisible to git, unmanaged, and
        gone the next time the cluster is rebuilt from source.
        """
        assert _values()["ui"]["readOnly"] is True
        assert "reader" in UI_CONFIG_TEMPLATE.read_text(), (
            "The config template must map readOnly onto AKHQ's `reader` group."
        )

    def test_the_image_is_pinned_to_a_version(self) -> None:
        """
        A mutable tag makes "roll back to the one that worked" inexpressible.
        The same rule app-deploy.sh enforces by refusing to tag a dirty tree
        with a commit sha.
        """
        image = _values()["ui"]["image"]
        assert ":" in image, f"{image!r} has no tag at all."
        tag = image.rsplit(":", 1)[1]
        assert tag not in {"latest", "main", "master", "edge", "stable"}, (
            f"Image tag {tag!r} is mutable — pin an explicit version."
        )

    def test_ui_heap_stays_below_the_memory_limit(self) -> None:
        """Same OOMKill trap as the brokers: the JVM must not be able to grow
        into the container limit."""
        ui = _values()["ui"]
        heap_mib = int(ui["jvm"]["xmx"].removesuffix("m"))
        limit_mib = int(ui["resources"]["limits"]["memory"].removesuffix("Mi"))
        assert limit_mib - heap_mib >= 128, (
            f"Only {limit_mib - heap_mib} MiB between heap and limit — the JVM "
            "needs non-heap headroom or the kernel kills the pod."
        )

    def test_probes_target_a_path_akhq_actually_serves(self) -> None:
        """
        AKHQ serves no /health — it 404s, and enabling Micronaut's
        endpoints.health does not change that. The failure mode is genuinely
        misleading: the container logs "Startup completed", serves the UI, and
        the kubelet kills it at failureThreshold anyway, so the logs show a
        clean boot next to a CrashLoopBackOff. Verified against the running
        pod: /ui and /api return 200, / returns 307, everything else 404s.
        """
        body = UI_TEMPLATE.read_text()
        assert "path: /health" not in body, (
            "AKHQ does not serve /health; the probe would kill a healthy pod."
        )
        assert body.count("path: /ui") == 3, (
            "Expected startup, readiness and liveness probes all on /ui."
        )

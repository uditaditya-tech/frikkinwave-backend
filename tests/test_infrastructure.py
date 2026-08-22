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
# The PERSISTENT stack. Named for its first inhabitant, but it now holds
# everything that must outlive a teardown: the zone, the cert, the budget
# alarm and the SNS alert topic.
DNS_DIR = REPO_ROOT / "infra" / "dns"
KAFKA_CHART = REPO_ROOT / "infra" / "helm" / "kafka"
KAFKA_VALUES = KAFKA_CHART / "values.yaml"
KAFKA_TEMPLATE = KAFKA_CHART / "templates" / "kafka.yaml"
APP_CHART = REPO_ROOT / "infra" / "helm" / "frikkinwave"

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


def _persistent_terraform() -> str:
    """Every .tf file in the persistent stack, concatenated."""
    return "\n".join(p.read_text() for p in sorted(DNS_DIR.glob("*.tf")))


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


class TestNothingInTheKafkaChartIsPubliclyExposed:
    """
    Retained after the AKHQ console was removed.

    The console is gone, but the rule it existed under is the durable part: the
    Kafka listener is plaintext with no authentication, so ANY component in this
    chart that grew an Ingress would put unauthenticated access to every event
    payload on the public internet. Whatever gets added here next — a console, an
    exporter, a REST proxy — must not be the thing that does it.
    """

    def test_no_template_declares_an_ingress(self) -> None:
        for template in KAFKA_CHART.glob("templates/*.yaml"):
            body = template.read_text()
            assert "kind: Ingress" not in body, (
                f"{template.name} declares an Ingress. Kafka has no auth on this "
                "cluster; nothing here may be reachable from outside it."
            )

    def test_no_template_requests_a_load_balancer_service(self) -> None:
        """A LoadBalancer Service is an Ingress by another name — it provisions a
        public NLB and never passes through the ALB or any auth."""
        for template in KAFKA_CHART.glob("templates/*.yaml"):
            body = template.read_text()
            assert "type: LoadBalancer" not in body, (
                f"{template.name} requests a LoadBalancer Service, which would "
                "publish it to the internet."
            )


class TestKafkaSecurity:
    """
    The security baseline, in the order Kafka's own model and NIST SP 800-207
    put it: encrypt in transit, authenticate, then authorize. Network isolation
    sits underneath as a second layer and is never the control itself.

    This exists because the cluster ran for a day with a plaintext, unauthenticated
    listener on which any pod could read every topic — and because the intuitive
    fix (a NetworkPolicy) would not have closed it: Strimzi leaves a listener's
    policy rule unrestricted unless networkPolicyPeers is set.
    """

    def test_no_listener_is_plaintext(self) -> None:
        assert "tls: false" not in KAFKA_TEMPLATE.read_text(), (
            "A plaintext listener sends event payloads — emails, names, message "
            "bodies — in clear over the network."
        )

    def test_the_listener_requires_authentication(self) -> None:
        assert _values()["security"]["authType"] in {"tls", "scram-sha-512"}
        assert "authentication:" in KAFKA_TEMPLATE.read_text()

    def test_the_listener_and_the_user_agree_on_the_auth_type(self) -> None:
        """
        A KafkaUser authenticating a way the listener does not accept is created
        happily, reports Ready, and simply never connects. Both templates read
        the same value so they cannot drift.
        """
        user = (KAFKA_CHART / "templates" / "user.yaml").read_text()
        assert ".Values.security.authType" in KAFKA_TEMPLATE.read_text()
        assert ".Values.security.authType" in user

    def test_mtls_keeps_the_private_key_out_of_the_environment(self) -> None:
        """
        Under mTLS the credential must be a mounted file. An env var is visible
        in `kubectl describe pod`, is inherited by every subprocess, and lands in
        logs that dump the environment.
        """
        if _values()["security"]["authType"] != "tls":
            return
        app_values = yaml.safe_load(
            (REPO_ROOT / "infra" / "helm" / "frikkinwave" / "values.yaml").read_text()
        )
        config = app_values["config"]
        assert config["KAFKA_SECURITY_PROTOCOL"] == "SSL", (
            "mTLS uses SSL, not SASL_SSL — there is no SASL mechanism involved."
        )
        for leaked in ("KAFKA_SASL_PASSWORD", "KAFKA_SASL_USERNAME", "KAFKA_SASL_MECHANISM"):
            assert leaked not in config, f"{leaked} is meaningless under mTLS; remove it."
        assert config["KAFKA_SSL_KEY_LOCATION"].startswith("/"), (
            "The private key must be a mounted path."
        )

    def test_authorization_is_enabled(self) -> None:
        """
        `simple` denies by default, so an authenticated client with no ACLs can
        do nothing. Without it, authentication only proves who a client is and
        then lets it do anything.
        """
        assert "authorization:\n      type: simple" in KAFKA_TEMPLATE.read_text()

    def test_the_listener_restricts_network_peers(self) -> None:
        """
        Strimzi's generated NetworkPolicy leaves a listener unrestricted
        (`from: null`) unless this is set — the reason enabling policy
        enforcement alone left port 9092 open to the whole cluster.
        """
        assert "networkPolicyPeers:" in KAFKA_TEMPLATE.read_text()

    def test_no_credential_is_committed(self) -> None:
        """
        This repository is PUBLIC. Strimzi generates the SCRAM password into a
        Secret in the cluster; it must never reach the chart.
        """
        for f in list(KAFKA_CHART.glob("**/*.yaml")):
            body = f.read_text().lower()
            for marker in ("password:", "sasl.jaas.config", "scram_password"):
                assert marker not in body, f"{f.name} looks like it carries a credential."


class TestTopicsMatchWhatIsPublished:
    """
    The chart's topic list IS the authorization surface: `simple` authorization
    denies anything ungranted, and an ACL has to name a topic.

    Compared against the `publish()` call sites rather than a registry — stage 5
    deleted the registry, and the call sites are the real source of truth.
    """

    def test_chart_topics_match_registry_exactly(self) -> None:
        from tests.test_architecture import _published_topics

        chart = set(_values()["topics"])
        published = _published_topics()
        assert published <= chart, (
            f"These topics are published but the chart grants no KafkaTopic or ACL "
            f"for them: {sorted(published - chart)}. `authorization: simple` denies "
            "anything ungranted, so every produce would be REJECTED in production "
            "and fine in tests."
        )
        assert not (chart - published), (
            f"The chart declares topics nothing publishes: {sorted(chart - published)}."
        )

    def test_declaring_a_kafkauser_requires_the_user_operator(self) -> None:
        """
        Without the User Operator a KafkaUser is inert in the most misleading way
        available: the object exists, `kubectl get kafkauser` prints its auth type
        and ACLs, nothing errors — and no SCRAM credential is created in Kafka and
        no Secret generated. A client would authenticate as a principal the broker
        has never heard of. This happened here on 2026-08-19.
        """
        chart_declares_a_user = (KAFKA_CHART / "templates" / "user.yaml").exists()
        if not chart_declares_a_user:
            return
        assert "userOperator:" in KAFKA_TEMPLATE.read_text(), (
            "The chart declares a KafkaUser but the Kafka CR does not enable the "
            "User Operator, so that user will never exist in Kafka."
        )

    def test_the_app_user_is_granted_no_destructive_operations(self) -> None:
        """
        Topics are KafkaTopic manifests reconciled by the Topic Operator. An
        application able to Delete or Alter them could drift the cluster away
        from what is in git, with nothing in a diff to show it.
        """
        body = (KAFKA_CHART / "templates" / "user.yaml").read_text()
        for op in ("Delete", "Alter", "All"):
            assert f"{op}]" not in body and f"{op}," not in body, (
                f"The app user is granted {op}, which it must not have."
            )


class TestObservability:
    """
    Phase 3. The gap these close is the one the stage-5 health signals cannot
    see: everything `check_outbox_lag` covers sits between `publish()` and the
    broker. A message that arrived and was never consumed leaves the outbox
    perfectly clean, so the producer side reports success while the work never
    happens.
    """

    def test_the_kafka_exporter_is_enabled(self) -> None:
        """kafka_consumergroup_lag has exactly one source here."""
        assert _values()["metrics"]["enabled"] is True
        assert "kafkaExporter:" in KAFKA_TEMPLATE.read_text()

    def test_consumer_lag_is_alerted_on(self) -> None:
        rules = (KAFKA_CHART / "templates" / "monitoring.yaml").read_text()
        assert "kafka_consumergroup_lag" in rules

    def test_an_empty_consumer_group_is_alerted_on_separately(self) -> None:
        """
        Zero members is NOT low lag. With nothing joined there may be no lag
        series at all, so the lag alert stays silent while a Deployment runs and
        consumes nothing — an ACL problem, a bad certificate, a crash loop.
        """
        rules = (KAFKA_CHART / "templates" / "monitoring.yaml").read_text()
        assert "kafka_consumergroup_members == 0" in rules

    def test_dead_lettering_is_alerted_on(self) -> None:
        """
        Dead-lettering is designed to be quiet — the consumer commits and moves
        on so the partition never stalls. Nothing else reports it, which makes
        this alert the only signal that a message was given up on.
        """
        rules = (KAFKA_CHART / "templates" / "monitoring.yaml").read_text()
        assert "KafkaDeadLetterReceived" in rules
        assert _values()["dltSuffix"] in rules

    def test_the_relay_reports_its_own_health(self) -> None:
        """
        The blind spot the Kafka-side alerts structurally cannot cover. A stalled
        relay means nothing reaches the broker, so there is nothing to be lagged
        on and `KafkaConsumerGroupLagHigh` stays silent while the entire pipeline
        is dead. Only a metric from the relay itself sees this.
        """
        rules = (APP_CHART / "templates" / "monitoring.yaml").read_text()
        assert "OutboxRelayDown" in rules
        assert "OutboxNotDraining" in rules

    def test_relay_absence_is_alerted_on_the_scrape_not_a_gauge(self) -> None:
        """
        A relay that is gone exports no gauges at all, so any threshold on its
        own metrics is unfalsifiable — the series simply stops. `up == 0` is the
        only expression that can detect the absence.
        """
        rules = (APP_CHART / "templates" / "monitoring.yaml").read_text()
        # `.*` rather than `[^}]+`: the expression is a Go template, so the job
        # label contains `}}` long before the PromQL selector closes.
        assert re.search(r"up\{job=.*\} == 0", rules), (
            "OutboxRelayDown must alert on the scrape failing, not on a gauge value."
        )
        assert "absent(up{job=" in rules, (
            "A relay scaled to zero leaves no `up` series at all, so `up == 0` "
            "cannot fire. Absence and zero are different failures."
        )

    def test_the_outbox_lag_cronjob_is_gone(self) -> None:
        """
        Retired by the gauge above: same reading, but graphable and alertable
        instead of a Job whose failures nobody watches. Two mechanisms measuring
        one thing is how they drift apart. The management command stays — it is
        still the fastest way to check from a shell.
        """
        assert not (APP_CHART / "templates" / "cronjob-outbox-lag.yaml").exists()
        assert (
            REPO_ROOT / "apps" / "events" / "management" / "commands" / "check_outbox_lag.py"
        ).exists()

    def test_alerts_have_somewhere_to_go(self) -> None:
        """
        The whole point of the rules above. Alertmanager evaluating alerts that
        reach nobody is the difference between "not silent" and "paging", and
        only the second one is worth anything.
        """
        assert "sns_configs" in _terraform()

        persistent = _persistent_terraform()
        assert "aws_sns_topic" in persistent
        assert re.search(r'protocol\s*=\s*"email"', persistent)

    def test_the_watchdog_is_not_routed_to_the_inbox(self) -> None:
        """
        Watchdog fires CONSTANTLY by design — it is a dead-man's switch, so a
        monitoring system that has silently died is detectable by the absence of
        its alerts. Supplying an Alertmanager `config` replaces the chart's
        default wholesale, including its handling of this, so routing everything
        to SNS would email forever and teach you to ignore the inbox.
        """
        tf = _terraform()
        assert "alertname = Watchdog" in tf
        assert "black-hole" in tf

    def test_alertmanager_publishes_with_its_own_identity(self) -> None:
        """
        IRSA, not the node role. sns:Publish on the node role would hand it to
        every pod on the cluster — the same reasoning as the LB controller.
        """
        tf = _terraform()
        assert "eks.amazonaws.com/role-arn" in tf
        # The :sub condition is what binds the role to one ServiceAccount.
        # Without it, any pod on the cluster could assume it.
        assert "system:serviceaccount:${var.observability_namespace}:" in tf

    def test_the_alert_subscription_outlives_a_teardown(self) -> None:
        """
        The reason the topic is not in the EKS stack. An SNS email subscription
        delivers NOTHING until a human clicks the confirmation link, so a topic
        destroyed by every teardown means re-confirming every session — an
        invisible failure gated on a recurring manual step.

        The IAM role stays in the disposable stack on purpose: it is bound to
        that cluster's OIDC provider and genuinely dies with it.
        """
        assert not (EKS_DIR / "budget.tf").exists()
        assert (DNS_DIR / "budget.tf").exists()

        eks = _terraform()
        assert 'resource "aws_sns_topic"' not in eks
        assert 'data "aws_sns_topic" "alerts"' in eks, (
            "The EKS stack must discover the topic, not own it."
        )
        assert 'resource "aws_iam_role" "alertmanager"' in eks

    def test_the_alert_subscription_is_optional(self) -> None:
        """
        `alert_email = ""` must still apply — a fresh clone should not need an
        address. The topic is created regardless (it costs nothing unsubscribed)
        so the EKS stack never has to ask whether alerting is configured.
        """
        persistent = _persistent_terraform()
        assert 'count = var.alert_email == "" ? 0 : 1' in persistent

        # The topic itself must NOT be gated, or the EKS data source breaks.
        topic = re.search(r'resource "aws_sns_topic" "alerts" \{(.*?)\n\}', persistent, re.S)
        assert topic and "count" not in topic.group(1)

    def test_charts_using_operator_crds_wait_for_the_operator(self) -> None:
        """
        PodMonitor and PrometheusRule are monitoring.coreos.com kinds owned by
        the Prometheus Operator. A helm_release creating them before the release
        that installs those CRDs fails outright:

            no matches for kind "PodMonitor" ... ensure CRDs are installed first

        This hid for a whole phase. Observability was added to a cluster where
        Kafka was already running, so the CRDs existed by the time the Kafka
        chart was next reconciled — only a from-scratch apply orders them wrong,
        and rebuilds are rare. Ordering that holds by accident is not ordering.
        """
        tf = _terraform()

        # Every helm_release rendering a LOCAL chart, paired with its body.
        releases = re.findall(r'resource\s+"helm_release"\s+"(\w+)"\s*\{(.*?)\n\}', tf, re.S)
        assert releases

        checked = 0
        for name, body in releases:
            local = re.search(r'chart\s*=\s*"\$\{path\.module\}/\.\./helm/([\w-]+)"', body)
            if not local:
                continue  # remote chart; its CRD needs are its own business

            chart_dir = REPO_ROOT / "infra" / "helm" / local.group(1)
            templates = "\n".join(t.read_text() for t in chart_dir.glob("templates/*.yaml"))
            if "monitoring.coreos.com" not in templates:
                continue

            checked += 1

            # Read the depends_on LIST, not the whole body. The comment above
            # that dependency necessarily names it, so a substring check over
            # the body passes on the prose explaining the bug — which is how the
            # first version of this test passed with the dependency deleted.
            uncommented = "\n".join(
                line for line in body.splitlines() if not line.strip().startswith("#")
            )
            deps = re.search(r"depends_on\s*=\s*\[(.*?)\]", uncommented, re.S)
            assert deps, f"helm_release.{name} has no depends_on at all."
            assert "helm_release.kube_prometheus_stack" in deps.group(1), (
                f"helm_release.{name} renders monitoring.coreos.com kinds but does "
                "not depend on the release installing those CRDs. This passes on a "
                "cluster that already has them and fails every fresh apply."
            )

        assert checked, "Expected at least one local chart using operator CRDs."

    def test_the_broker_monitor_does_not_also_match_the_exporter(self) -> None:
        """
        The exporter pod carries `strimzi.io/kind: Kafka` too, so selecting on
        that scrapes it under BOTH jobs. Every consumer-lag series is then
        duplicated and `sum by (consumergroup)` returns double the real lag —
        so the alert fires at half its configured threshold, which looks like a
        tuning problem rather than a selector bug. Select on component-type.
        """
        # Comment lines stripped: the explanation of this bug necessarily
        # contains the very string being asserted against.
        rules = "\n".join(
            line
            for line in (KAFKA_CHART / "templates" / "monitoring.yaml").read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "strimzi.io/kind" not in rules, (
            "A PodMonitor selecting strimzi.io/kind matches the exporter as well "
            "as the brokers, duplicating every metric."
        )
        assert "strimzi.io/component-type: kafka" in rules

    def test_the_exporter_only_scrapes_our_own_groups(self) -> None:
        """
        Left at `.*` the exporter also reports the throwaway console-consumer
        groups a human creates while debugging, which then age into stale series
        and dirty the lag alert.
        """
        assert _values()["metrics"]["groupRegex"].startswith(_values()["consumerGroupPrefix"])

    def test_prometheus_uses_the_storage_class_that_actually_exists(self) -> None:
        """
        Prometheus needs a PVC. Naming a class Terraform does not create leaves
        it Pending forever — the exact failure stage 0 existed to fix, and one
        that would land here silently.
        """
        tf = _terraform()
        assert "kubernetes_storage_class_v1.gp3.metadata[0].name" in tf, (
            "Prometheus must reference the gp3 class Terraform creates, not a literal."
        )

    def test_the_grafana_dashboard_is_valid_json(self) -> None:
        """
        It ships as a ConfigMap read by a sidecar. Malformed JSON is not a
        deploy failure — Grafana simply skips it and the dashboard is missing,
        with the error buried in sidecar logs nobody reads.
        """
        import json

        path = REPO_ROOT / "infra" / "helm" / "frikkinwave" / "dashboards" / "event-pipeline.json"
        dashboard = json.loads(path.read_text())
        assert dashboard["panels"], "dashboard has no panels"
        exprs = " ".join(t["expr"] for p in dashboard["panels"] for t in p.get("targets", []))
        assert "kafka_consumergroup_lag" in exprs


class TestOpenSearchDomain:
    """
    The search domain's invariants.

    It holds no source of truth — every document is rebuilt from Postgres by
    `reindex_profiles` — so nothing here is about durability. It is about the
    domain being reachable by the cluster and by nothing else, and about the
    engine not drifting away from the client that talks to it.
    """

    def test_the_domain_lives_in_the_vpc(self) -> None:
        """
        A domain without vpc_options gets a public endpoint on the internet,
        guarded only by its access policy — and this one's access policy is
        deliberately permissive, because fine-grained access control is doing
        the authorization. Lose the VPC and that pairing becomes an open door.
        """
        tf = _terraform()
        domain = re.search(r'resource\s+"aws_opensearch_domain"(.*?)\n\}', tf, re.DOTALL)
        assert domain, "No aws_opensearch_domain in the EKS stack."
        assert "vpc_options" in domain.group(1), (
            "The OpenSearch domain declares no vpc_options, so AWS gives it a "
            "PUBLIC endpoint. Its access policy allows es:* from any principal "
            "on the assumption that only the VPC can reach it."
        )

    def test_only_the_cluster_may_reach_the_domain(self) -> None:
        tf = _terraform()
        sg = re.search(r'resource\s+"aws_security_group"\s+"opensearch"(.*?)\n\}\n', tf, re.DOTALL)
        assert sg, "No security group for the OpenSearch domain."
        body = sg.group(1)
        assert "cluster_security_group_id" in body, (
            "The OpenSearch security group does not source from the EKS cluster "
            "security group, so it is not restricted to cluster workloads."
        )
        assert "cidr_blocks" not in body.split("egress")[0], (
            "The OpenSearch ingress rule admits a CIDR range rather than only "
            "the cluster security group."
        )

    def test_fine_grained_access_control_has_its_prerequisites(self) -> None:
        """
        FGAC requires encryption at rest, node-to-node encryption and enforced
        HTTPS. Miss one and the domain create fails at apply — twenty minutes
        into a rebuild, which is a slow way to learn it.
        """
        tf = _terraform()
        domain = re.search(r'resource\s+"aws_opensearch_domain"(.*?)\n\}\n\Z', tf, re.DOTALL)
        body = domain.group(1) if domain else _terraform()

        assert "advanced_security_options" in body
        for required in ("encrypt_at_rest", "node_to_node_encryption", "enforce_https"):
            assert required in body, (
                f"Fine-grained access control is enabled but {required} is not "
                "configured. AWS rejects that combination at create time."
            )

    def test_the_engine_major_matches_the_client_major(self) -> None:
        """
        The pinned opensearch-py major and the managed engine major have to
        agree. They are declared in two files that are never edited together —
        requirements/base.txt and variables.tf — so nothing but this notices
        when one moves.
        """
        engine = _tf_default("opensearch_engine_version").strip('"')
        engine_major = engine.removeprefix("OpenSearch_").split(".")[0]

        requirements = (REPO_ROOT / "requirements" / "base.txt").read_text()
        client = re.search(r"^opensearch-py==(\d+)\.", requirements, re.MULTILINE)
        assert client, "opensearch-py is not pinned in requirements/base.txt."

        assert engine_major == client.group(1), (
            f"The managed engine is OpenSearch {engine_major}.x but opensearch-py "
            f"is pinned to {client.group(1)}.x. Keep the majors aligned."
        )

    def test_the_search_url_is_built_in_terraform_not_helm(self) -> None:
        """
        Same rule DATABASE_URL follows: the generated password is assembled into
        a URL inside Terraform and handed to the pods through the Kubernetes
        Secret. Routing it through `helm upgrade --set` would put it in the
        release's stored manifest and in shell history.
        """
        tf = _terraform()
        assert "opensearch_url" in tf, "No opensearch_url local in the EKS stack."
        assert "OPENSEARCH_URL" in tf, (
            "Terraform builds an OpenSearch URL but never puts it in the app Secret."
        )

        chart_values = (REPO_ROOT / "infra" / "helm" / "frikkinwave" / "values.yaml").read_text()
        assert "opensearch" not in chart_values.lower() or "password" not in chart_values.lower(), (
            "The chart values mention an OpenSearch password. Credentials belong "
            "in the Terraform-owned Secret, not in Helm values."
        )

    def test_no_openai_plumbing_survives(self) -> None:
        """
        The AI work is gone from the application; the secret that fed it must not
        outlive it. A provisioned credential nothing reads is a credential nobody
        thinks to rotate.
        """
        tf = _terraform()
        # Identifiers, not prose. Comments explaining why the embeddings are
        # gone are history worth keeping; a variable or a parameter is plumbing.
        plumbing = [
            "OPENAI_API_KEY",
            "var.openai",
            'variable "openai',
            '"openai_api_key"',
        ]
        found = [token for token in plumbing if token in tf]
        assert not found, (
            "The EKS stack still provisions OpenAI plumbing that no code reads: " + ", ".join(found)
        )

"""
Kafka consumer runtime (KAFKA.md stage 4).

The property under test is not "messages get handled" — it is that **the
partition always advances**. Under Celery a permanently failing task was an
isolated nuisance; here, refusing to move past a bad message halts every message
behind it on the same partition. Every failure path below therefore ends in a
committed offset.

No broker: the Consumer is faked, so CI needs no Kafka and makes no network
calls.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.events import consumer as runtime
from apps.events.consumer import SubscriptionsNotFound, load_subscriptions


class FakeMessage:
    def __init__(self, topic: str, value: bytes | None, err: Any = None) -> None:
        self._topic, self._value, self._err = topic, value, err

    def topic(self) -> str:
        return self._topic

    def value(self) -> bytes | None:
        return self._value

    def error(self) -> Any:
        return self._err


class FakeConsumer:
    """Records subscribe/commit/close so the ordering guarantees are assertable."""

    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = list(messages)
        self.subscribed: list[str] = []
        self.committed: list[str] = []
        self.closed = False

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed = list(topics)

    def poll(self, timeout: float) -> FakeMessage | None:
        return self.messages.pop(0) if self.messages else None

    def commit(self, message: Any = None, asynchronous: bool = True) -> None:
        self.committed.append(message.topic())

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def dead_letters(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def fake_produce(*, topic: str, payload: dict[str, Any], key: str | None = None) -> None:
        sent.append({"topic": topic, **payload})

    monkeypatch.setattr("apps.events.kafka.produce", fake_produce)
    return sent


class TestSubscriptionsAreDeclaredPerApp:
    """The stage 4 inversion: no central table, each service declares its own."""

    def test_each_service_declares_its_own(self) -> None:
        assert "profile.updated" in load_subscriptions("search")
        assert "review.created" in load_subscriptions("reviews")
        assert "follow.created" in load_subscriptions("social")
        assert "contact_request.created" in load_subscriptions("notifications")

    def test_a_group_with_no_such_app_fails_loudly(self) -> None:
        """
        A misspelled group would otherwise start, subscribe to nothing, and sit
        there looking perfectly healthy forever.
        """
        with pytest.raises(SubscriptionsNotFound):
            load_subscriptions("nosuchapp")

    def test_the_producer_never_imports_a_consumers_module(self) -> None:
        """
        The whole point of stage 4. If the relay imported subscriptions, adding a
        second consumer to a topic would be a change to the producer again.

        Checked against the import graph, not the file text — the word
        "consumers" appears legitimately in prose explaining this very rule.
        """
        import ast
        import pathlib

        events = pathlib.Path(runtime.__file__).parent
        offenders = []
        for path in events.glob("*.py"):
            if path.name == "consumer.py":
                continue  # the runtime resolves them; that is its whole job
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(".consumers"):
                    offenders.append(f"{path.name} -> {node.module}")
                if isinstance(node, ast.Import):
                    offenders += [
                        f"{path.name} -> {a.name}"
                        for a in node.names
                        if a.name.endswith(".consumers")
                    ]
        assert not offenders, (
            f"The producer side imports consumer declarations: {offenders}. "
            "That reintroduces exactly the coupling stage 4 removes."
        )


@pytest.mark.django_db
class TestThePartitionAlwaysAdvances:
    def test_a_handled_message_is_committed_after_the_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "apps.search.consumers.SUBSCRIPTIONS",
            {"profile.updated": lambda **p: calls.append(p)},
        )
        fake = FakeConsumer([FakeMessage("profile.updated", json.dumps({"x": 1}).encode())])

        assert runtime.run(group="search", consumer=fake, max_messages=5) == 1
        assert calls == [{"x": 1}]
        assert fake.committed == ["profile.updated"]

    def test_a_failing_handler_is_dead_lettered_and_still_committed(
        self, monkeypatch: pytest.MonkeyPatch, dead_letters: list[dict[str, Any]], settings: Any
    ) -> None:
        """
        The one that matters. Not committing here would stall every message
        behind it on this partition — a single bad payload taking out a whole
        service.
        """
        settings.KAFKA_CONSUMER_MAX_ATTEMPTS = 2
        settings.KAFKA_CONSUMER_RETRY_BACKOFF = 0

        def boom(**payload: Any) -> None:
            raise ValueError("handler exploded")

        monkeypatch.setattr("apps.search.consumers.SUBSCRIPTIONS", {"profile.updated": boom})
        fake = FakeConsumer([FakeMessage("profile.updated", b'{"x": 1}')])

        runtime.run(group="search", consumer=fake, max_messages=5)

        assert fake.committed == ["profile.updated"], "the partition must advance"
        assert len(dead_letters) == 1
        dlt = dead_letters[0]
        assert dlt["topic"] == "profile.updated.dlt"
        assert dlt["original_topic"] == "profile.updated"
        assert dlt["consumer_group"] == "search"
        assert "handler exploded" in dlt["error"]

    def test_a_transient_failure_is_retried_before_dead_lettering(
        self, monkeypatch: pytest.MonkeyPatch, dead_letters: list[dict[str, Any]], settings: Any
    ) -> None:
        settings.KAFKA_CONSUMER_MAX_ATTEMPTS = 3
        settings.KAFKA_CONSUMER_RETRY_BACKOFF = 0
        attempts = {"n": 0}

        def flaky(**payload: Any) -> None:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("database blip")

        monkeypatch.setattr("apps.search.consumers.SUBSCRIPTIONS", {"profile.updated": flaky})
        fake = FakeConsumer([FakeMessage("profile.updated", b'{"x": 1}')])

        runtime.run(group="search", consumer=fake, max_messages=5)

        assert attempts["n"] == 3
        assert dead_letters == [], "a recoverable failure must not be dead-lettered"
        assert fake.committed == ["profile.updated"]

    def test_malformed_json_is_dead_lettered_without_retrying(
        self, dead_letters: list[dict[str, Any]], settings: Any
    ) -> None:
        """Unparseable now is unparseable forever; retrying only stalls the partition."""
        settings.KAFKA_CONSUMER_RETRY_BACKOFF = 0
        fake = FakeConsumer([FakeMessage("profile.updated", b"not json at all")])

        runtime.run(group="search", consumer=fake, max_messages=5)

        assert fake.committed == ["profile.updated"]
        assert "malformed json" in dead_letters[0]["error"]
        assert dead_letters[0]["payload"] == "not json at all"

    def test_a_topic_with_no_handler_is_dead_lettered(
        self, monkeypatch: pytest.MonkeyPatch, dead_letters: list[dict[str, Any]]
    ) -> None:
        monkeypatch.setattr(
            "apps.search.consumers.SUBSCRIPTIONS", {"profile.updated": lambda **p: None}
        )
        fake = FakeConsumer([FakeMessage("something.else", b"{}")])

        runtime.run(group="search", consumer=fake, max_messages=5)

        assert fake.committed == ["something.else"]
        assert "no handler" in dead_letters[0]["error"]


@pytest.mark.django_db
class TestLifecycle:
    def test_it_subscribes_to_exactly_its_declared_topics(self) -> None:
        fake = FakeConsumer([])
        runtime.run(group="social", consumer=fake, max_messages=0)
        assert fake.subscribed == sorted(load_subscriptions("social"))

    def test_the_consumer_is_closed_on_the_way_out(self) -> None:
        """
        close() leaves the group cleanly so the broker rebalances immediately
        instead of waiting out the session timeout — which on a rollout is the
        difference between seconds and a stalled partition.
        """
        fake = FakeConsumer([])
        runtime.run(group="social", consumer=fake, max_messages=0)
        assert fake.closed

    def test_it_closes_even_when_a_handler_explodes_uncaught(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def detonate(*args: Any, **kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(runtime, "_handle", detonate)
        fake = FakeConsumer([FakeMessage("follow.created", b"{}")])

        with pytest.raises(KeyboardInterrupt):
            runtime.run(group="social", consumer=fake, max_messages=5)
        assert fake.closed

    def test_a_poll_error_does_not_commit_anything(self) -> None:
        fake = FakeConsumer([FakeMessage("follow.created", None, err="broker gone")])
        runtime.run(group="social", consumer=fake, max_messages=5)
        assert fake.committed == []

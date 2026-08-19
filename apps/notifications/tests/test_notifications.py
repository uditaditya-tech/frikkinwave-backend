"""
Tests for the extracted notifications service.

What is deliberately NOT tested here: "the row was deleted before the task ran".
That was the failure mode of the old design, where each task took an id and
re-read the row. With self-contained payloads there is no row to go missing —
the class of bug is gone, not merely handled. What replaces it is a payload that
arrives incomplete, which is what these cover.
"""

from __future__ import annotations

import pytest
from django.core.mail import EmailMessage

from apps.events.registry import EVENT_HANDLERS
from apps.notifications import services
from apps.notifications.renderers import RENDERERS
from apps.notifications.services import UnknownNotificationKind


class TestContract:
    def test_every_topic_routed_here_has_a_renderer(self) -> None:
        """
        The registry and the renderers must not drift. A topic pointed at this
        service with no renderer raises at delivery time — in a worker, where
        nobody is watching — so it is caught here instead.
        """
        routed = {
            topic for topic, task in EVENT_HANDLERS.items() if task.startswith("notifications.")
        }
        assert routed, "No topics route to notifications — the registry lost them."
        assert routed <= set(RENDERERS), f"No renderer for: {sorted(routed - set(RENDERERS))}"

    def test_renderer_keys_match_their_topics(self) -> None:
        """Renderer keys are the topic strings, so copy is greppable from a topic."""
        for topic, task in EVENT_HANDLERS.items():
            if task.startswith("notifications."):
                assert topic in RENDERERS


class TestDeliver:
    def test_sends_rendered_email(self, mailoutbox: list[EmailMessage]) -> None:
        services.deliver(
            kind="contact_request.created",
            recipient_email="them@example.com",
            sender_username="jazzcat",
            message="Let's jam",
        )
        assert len(mailoutbox) == 1
        email = mailoutbox[0]
        assert email.to == ["them@example.com"]
        assert "jazzcat" in email.subject
        assert "Let's jam" in email.body

    def test_optional_field_is_omitted_not_stringified(
        self, mailoutbox: list[EmailMessage]
    ) -> None:
        """
        proposed_date is nullable upstream. It must be left out rather than
        rendered as "None" — the old code called .isoformat() on it unguarded.
        """
        services.deliver(
            kind="engagement.requested",
            recipient_email="musician@example.com",
            requester_username="promoter",
            proposed_date="",
            rate_offer="",
        )
        body = mailoutbox[0].body
        assert "None" not in body
        assert "Proposed date" not in body
        assert "Rate offered" not in body

    def test_missing_recipient_is_dropped_not_retried(self, mailoutbox: list[EmailMessage]) -> None:
        """
        An empty address means the producer published an incomplete event.
        Retrying cannot conjure one, so it is dropped with a log line rather
        than poisoning the queue forever.
        """
        services.deliver(
            kind="contact_request.created",
            recipient_email="",
            sender_username="jazzcat",
        )
        assert len(mailoutbox) == 0

    def test_unknown_kind_raises(self, mailoutbox: list[EmailMessage]) -> None:
        """Contract drift between registry and renderers must be loud."""
        with pytest.raises(UnknownNotificationKind):
            services.deliver(kind="nope.not_a_thing", recipient_email="a@example.com")
        assert len(mailoutbox) == 0

    def test_reveal_on_accept_discloses_the_address(self, mailoutbox: list[EmailMessage]) -> None:
        """The accepter's address is disclosed only in the acceptance email."""
        services.deliver(
            kind="contact_request.accepted",
            recipient_email="sender@example.com",
            accepter_username="jazzcat",
            accepter_email="jazzcat@example.com",
        )
        assert "jazzcat@example.com" in mailoutbox[0].body


class TestIsolation:
    def test_service_module_imports_no_models(self) -> None:
        """
        The property that makes this extractable: it can render and send without
        the ORM. If this fails, the service has quietly re-acquired a database
        dependency and is no longer separable.
        """
        import ast
        import pathlib

        pkg = pathlib.Path(services.__file__).parent
        for module in ("services.py", "renderers.py", "tasks.py"):
            tree = ast.parse((pkg / module).read_text())
            imported = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            offenders = [
                m
                for m in imported
                if m.startswith("apps.") and not m.startswith("apps.notifications")
            ]
            assert not offenders, f"{module} imports another app: {offenders}"
            assert not any("models" in m for m in imported), f"{module} imports models"

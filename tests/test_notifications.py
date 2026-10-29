from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend import notify


class FakeChannel:
    name = "fake"

    def __init__(self) -> None:
        self.messages = []

    def send(self, title, body, priority, tags, click) -> None:
        self.messages.append({"title": title, "body": body, "priority": priority,
                              "tags": tags, "click": click})


class FailingChannel:
    name = "failing"

    def send(self, title, body, priority, tags, click) -> None:
        raise RuntimeError("private-token-must-not-be-logged")


def alert(resource: str, risk: str = "Watch", days: int | None = 45, **extra):
    return {
        "resource": resource,
        "risk_level": risk,
        "lead_time_days": days,
        "predicted_crossing_date": "2026-12-01" if days is not None else "not estimable",
        "rack": "R1",
        "resource_type": "storage",
        "unit": "TB",
        "capacity_limit": 100.0,
        "current_value": 70.0,
        "safety_threshold": 90.0,
        "utilisation_pct": 77.8,
        "weekly_growth": 2.5,
        "assessment_date": "2026-10-28",
        "forecast_note": None,
        "changes": [{"type": "growth_rate_change", "date": "2026-10-20"}],
        "range": {"low_days": 30, "high_days": 60},
        "confidence": "Medium",
        "likely_contributor": {"tenant": "acme"},
        "recommended_action": "Review headroom (advisory).",
        "alert_type": "sustained_growth",
        "forecast_fingerprint": "fingerprint123456",
        **extra,
    }


@pytest.fixture(autouse=True)
def isolated_notifications(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOTIFY_ENABLED", "true")
    monkeypatch.setenv("NOTIFY_MIN_LEVEL", "Watch")
    monkeypatch.setenv("NOTIFY_COOLDOWN_HOURS", "24")
    monkeypatch.setenv("NOTIFY_DRY_RUN", "false")
    monkeypatch.setenv("NOTIFY_CHANNELS", "console")
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)


def test_new_alert_then_unchanged_alert_is_cooldown_deduplicated():
    channel = FakeChannel()
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    first = notify.dispatch([alert("STO-R1-01")], channel_overrides=[channel], now=now)
    repeated = notify.dispatch([alert("STO-R1-01")], channel_overrides=[channel],
                               now=now + timedelta(minutes=5))
    assert first["sent"] == 1
    assert len(channel.messages) == 1
    assert repeated["notifications_eligible"] == 0
    assert notify.notification_history(limit=5)[0]["status"] == "skipped_cooldown"
    assert notify.notification_history(limit=5)[0]["event_type"] == "ongoing"
    assert notify.notification_history(limit=5)[-1]["status"] == "sent"


def test_escalation_notifies_even_during_cooldown():
    channel = FakeChannel()
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    notify.dispatch([alert("PWR-R2-01", "Watch", 40)], channel_overrides=[channel], now=now)
    result = notify.dispatch([alert("PWR-R2-01", "High Risk", 28)],
                             channel_overrides=[channel], now=now + timedelta(minutes=2))
    assert result["sent"] == 1
    assert len(channel.messages) == 2
    assert "HIGH RISK Capacity Risk" in channel.messages[-1]["title"]
    assert notify.notification_history(limit=1)[0]["event_type"] == "escalated"


def test_lead_time_step_and_recovered_alert_retrigger():
    channel = FakeChannel()
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    notify.dispatch([alert("PWR-R2-01", "High Risk", 35)], channel_overrides=[channel], now=now)
    notify.dispatch([alert("PWR-R2-01", "High Risk", 28)], channel_overrides=[channel],
                    now=now + timedelta(minutes=1))
    assert notify.notification_history(limit=1)[0]["event_type"] == "step_down"
    notify.dispatch([alert("PWR-R2-01", "Healthy", None)], channel_overrides=[channel],
                    now=now + timedelta(minutes=2))
    notify.dispatch([alert("PWR-R2-01", "Watch", 80)], channel_overrides=[channel],
                    now=now + timedelta(minutes=3))
    assert len(channel.messages) == 3
    assert notify.notification_history(limit=1)[0]["event_type"] == "new"


def test_healthy_resource_and_temporary_spike_do_not_notify():
    channel = FakeChannel()
    healthy = alert("NET-R3-01", "Healthy", None, alert_type="possible_spike_unconfirmed")
    notify.dispatch([healthy], channel_overrides=[channel])
    assert channel.messages == []
    assert notify.notification_history() == []


def test_batch_digest_and_priority_mapping():
    channel = FakeChannel()
    result = notify.dispatch([
        alert("STO-R1-01", "Critical", 7),
        alert("VM-R3-01", "High Risk", 20),
        alert("NET-R1-01", "Watch", 60),
    ], channel_overrides=[channel])
    assert result["messages_dispatched"] == 2
    assert len(channel.messages) == 2
    assert "digest" in channel.messages[0]["title"].lower()
    assert channel.messages[0]["priority"] == "urgent"
    assert "CRITICAL Storage STO-R1-01" in channel.messages[0]["body"]
    assert "70.00 TB / 100.00 TB (70%)" in channel.messages[0]["body"]
    assert "CRITICAL Capacity Risk" in channel.messages[1]["title"]
    assert channel.messages[1]["priority"] == "urgent"
    history = notify.notification_history(limit=10)
    assert sum(row["event_type"] == "digest" for row in history) == 3


def test_minimum_level_and_dry_run_are_respected(monkeypatch, capsys):
    channel = FakeChannel()
    monkeypatch.setenv("NOTIFY_MIN_LEVEL", "Critical")
    filtered = notify.dispatch([alert("STO-R1-01", "Watch")], channel_overrides=[channel])
    assert filtered["notifications_eligible"] == 0
    assert not channel.messages

    monkeypatch.setenv("NOTIFY_MIN_LEVEL", "Watch")
    dry_run = notify.dispatch([alert("VM-R3-01", "High Risk")],
                              channel_overrides=[channel], dry_run=True)
    assert dry_run["dry_run_messages"] == 1
    assert not channel.messages
    assert "HIGH RISK" in capsys.readouterr().out
    assert notify.notification_history(limit=1)[0]["status"] == "dry_run"


def test_failed_channel_is_logged_without_leaking_error_secrets(caplog):
    caplog.set_level(logging.ERROR, logger=notify.LOGGER.name)
    result = notify.run_notification_cycle(
        alerts_override=[alert("STO-R1-01")], channel_overrides=[FailingChannel()],
        state_scope="failed-channel-test",
    )
    assert result["failed"] == 1
    record = notify.notification_history(limit=1)[0]
    assert record["status"] == "failed"
    assert record["error"] == "delivery failed"
    assert "private-token-must-not-be-logged" not in caplog.text
    assert "private-token-must-not-be-logged" not in str(record)


def test_tenant_scope_only_sends_own_message_and_history():
    channel = FakeChannel()
    own_alert = alert("STO-R1-01", tenant_scope="acme",
                      likely_contributor={"tenant": "acme"})
    notify.dispatch([own_alert], tenant="acme", channel_overrides=[channel])
    assert len(channel.messages) == 1
    assert "acme" in channel.messages[0]["body"]
    assert "globex" not in channel.messages[0]["body"]
    scoped_history = notify.notification_history(tenant="acme")
    assert scoped_history and all(row["tenant_scope"] == "acme" for row in scoped_history)
    assert notify.notification_history(tenant="globex") == []


def test_notification_log_includes_forecast_fingerprint():
    notify.dispatch([alert("STO-R1-01")], channel_overrides=[FakeChannel()])
    record = notify.notification_history(limit=1)[0]
    assert record["forecast_fingerprint"] == "fingerprint123456"


def test_ntfy_channel_uses_expected_http_headers_without_network(monkeypatch):
    observed = {}

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, **kwargs):
        observed.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    channel = notify.NtfyChannel("https://ntfy.sh", "unguessable/topic", "test-token")
    channel.send("Capacity title", "Advisory message", "urgent", "warning", "https://dashboard.local")
    assert observed["url"] == "https://ntfy.sh/unguessable%2Ftopic"
    assert observed["headers"]["Priority"] == "urgent"
    assert observed["headers"]["Click"] == "https://dashboard.local"
    assert observed["headers"]["Authorization"] == "Bearer test-token"


def test_capacity_message_uses_forecast_details_and_deep_link():
    item = alert("STO-R1-01", "Critical", 12)
    message = notify._make_message(item, "new", "acme")

    assert "CRITICAL Capacity Risk: Storage" in message.title
    assert "Rack R1" in message.title
    assert "70.00 TB / 100.00 TB (70% of capacity)" in message.body
    assert "safety threshold 90.00 TB" in message.body
    assert "about 12 days (around Dec 01, 2026)" in message.body
    assert "recent growth 2.50 TB/week" in message.body
    assert "Associated workload: acme" in message.body
    assert "Assessed: Oct 28, 2026" in message.body
    assert "Status: New alert." in message.body

    query = urllib.parse.parse_qs(urllib.parse.urlparse(message.click).query)
    assert query["resource"] == ["STO-R1-01"]
    assert query["tenant"] == ["acme"]
    assert query["alert_id"] == [notify._alert_id("STO-R1-01", "acme")]
    assert query["event"] == ["new"]
    assert query["fingerprint"] == ["fingerprint123456"]


def test_capacity_message_omits_unavailable_estimates_without_inventing():
    item = alert(
        "NET-R3-01",
        "Watch",
        None,
        resource_type="network",
        unit="Gbps",
        current_value=78.0,
        capacity_limit=None,
        safety_threshold=80.0,
        predicted_crossing_date="not estimable",
        range={"low_days": None, "high_days": None},
        weekly_growth=None,
        changes=[],
        likely_contributor={"tenant": None, "wording": "cause unknown"},
        forecast_note="no statistically supported growth; exhaustion date not estimated",
        assessment_date=None,
    )
    message = notify._make_message(item, "new", None)

    assert "WATCH Capacity Risk: Network" in message.title
    assert "78.00 Gbps" in message.body
    assert "Time to safety threshold is not reliably estimated" in message.body
    assert "Estimated threshold crossing in about" not in message.body
    assert "Likely contributor" not in message.body
    assert "Assessed:" not in message.body


def test_resource_type_display_names_preserve_domain_terminology():
    vm_message = notify._make_message(
        alert("VM-R3-01", "High Risk", resource_type="vm", rack="R3"),
        "escalated",
        None,
    )
    thermal_message = notify._make_message(
        alert("TMP-R2-01", "Watch", resource_type="thermal", rack="R2"),
        "regime_change",
        None,
    )

    assert "Capacity Risk: VM - Rack R3" in vm_message.title
    assert "Status: Risk escalated." in vm_message.body
    assert "Cooling/Temperature TMP-R2-01" in thermal_message.body
    assert "Status: Trend changed." in thermal_message.body


def test_resolved_and_uncertain_alerts_have_unambiguous_titles():
    resolved = notify._make_message(alert("STO-R1-01", "Healthy", None), "resolved", None)
    uncertain = notify._make_message(
        alert("NET-R3-01", "Healthy", None, alert_type="possible_spike_unconfirmed"),
        "possible_spike_unconfirmed",
        None,
    )

    assert "RESOLVED Capacity Alert" in resolved.title
    assert "UNCERTAIN Capacity Signal" in uncertain.title
    assert "Status: Unconfirmed spike." in uncertain.body


def test_notification_api_is_read_only_scoped_and_hides_tokens(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "public-but-random-topic")
    monkeypatch.setenv("NTFY_TOKEN", "top-secret-token")
    monkeypatch.setenv("NOTIFY_CHANNELS", "ntfy")
    with TestClient(app) as client:
        status = client.get("/api/notifications/status")
        assert status.status_code == 200
        assert status.json()["channels"] == ["ntfy"]
        assert status.json()["ntfy_configured"] is True
        assert "ntfy_topic" not in status.json()
        assert "public-but-random-topic" not in str(status.json())
        assert "top-secret-token" not in str(status.json())
        tenant_status = client.get("/api/notifications/status", params={"tenant": "acme"})
        assert tenant_status.json()["ntfy_configured"] is False
        assert client.get("/api/notifications/status", params={"tenant": "unknown"}).status_code == 404
        assert client.get("/api/notifications", params={"tenant": "unknown"}).status_code == 404
        assert client.get("/api/notifications", params={"tenant": "acme"}).status_code == 200
        for method in ("post", "put", "delete"):
            assert getattr(client, method)("/api/notifications/status").status_code == 405

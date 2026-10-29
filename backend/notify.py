from __future__ import annotations

import argparse
import asyncio
import email.message
import hashlib
import json
import logging
import os
import smtplib
import sqlite3
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import formatdate
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from backend.data import DATA_DIR, ROOT, load_dataset

LOGGER = logging.getLogger("capacity_intelligence.notifications")
LEVELS = {"Healthy": 0, "Watch": 1, "High Risk": 2, "Critical": 3, "Insufficient Data": 0}
STEP_DAYS = (30, 14, 7)
_CYCLE_LOCK = threading.Lock()
_RUNTIME: dict[str, Any] = {"last_cycle_at": None, "last_result": None}


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


_load_dotenv()


class NotificationChannel(Protocol):
    name: str

    def send(self, title: str, body: str, priority: str, tags: str, click: str) -> None: ...


@dataclass(frozen=True)
class Notification:
    resource_id: str
    tenant_scope: str
    risk_level: str
    days_remaining: int | None
    event_type: str
    title: str
    body: str
    priority: str
    tags: str
    click: str
    forecast_fingerprint: str


class NtfyChannel:
    name = "ntfy"

    def __init__(self, server: str, topic: str, token: str = "") -> None:
        self.url = f"{server.rstrip('/')}/{urllib.parse.quote(topic, safe='')}"
        self.token = token

    def send(self, title: str, body: str, priority: str, tags: str, click: str) -> None:
        headers = {
            "Title": title,
            "Priority": {"urgent": "urgent", "high": "high", "default": "default"}.get(priority, "default"),
            "Tags": tags,
            "Click": click,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = httpx.post(self.url, content=body.encode("utf-8"), headers=headers, timeout=10)
        response.raise_for_status()


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str) -> None:
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, title: str, body: str, priority: str, tags: str, click: str) -> None:
        response = httpx.post(
            self.url,
            json={"chat_id": self.chat_id, "text": f"{title}\n\n{body}\n\nDashboard: {click}",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        response.raise_for_status()


class EmailChannel:
    name = "email"

    def __init__(self, host: str, port: int, user: str, password: str, recipient: str) -> None:
        self.host, self.port, self.user, self.password, self.recipient = host, port, user, password, recipient

    def send(self, title: str, body: str, priority: str, tags: str, click: str) -> None:
        message = email.message.EmailMessage()
        message["Subject"] = title
        message["From"] = self.user or "capacity-intelligence@localhost"
        message["To"] = self.recipient
        message["Date"] = formatdate(localtime=False)
        message.set_content(f"{body}\n\nDashboard: {click}")
        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            smtp.ehlo()
            if self.port == 587:
                smtp.starttls()
                smtp.ehlo()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(message)


class ConsoleChannel:
    name = "console"

    def send(self, title: str, body: str, priority: str, tags: str, click: str) -> None:
        print(f"\n[{priority.upper()}] {title}\n{body}\nDashboard: {click}", flush=True)


def _truthy(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _tenant_topics() -> dict[str, str]:
    topics: dict[str, str] = {}
    for entry in os.getenv("NOTIFY_TENANT_TOPICS", "").split(","):
        tenant, separator, topic = entry.strip().partition(":")
        if separator and tenant and topic:
            topics[tenant.strip()] = topic.strip()
    return topics


def _configured_channels(tenant: str | None = None) -> list[NotificationChannel]:
    names = [name.strip().lower() for name in os.getenv("NOTIFY_CHANNELS", "ntfy").split(",") if name.strip()]
    channels: list[NotificationChannel] = []
    if tenant:
        tenant_topic = _tenant_topics().get(tenant, "")
        if "ntfy" in names and tenant_topic:
            channels.append(NtfyChannel(os.getenv("NTFY_SERVER", "https://ntfy.sh"),
                                        tenant_topic, os.getenv("NTFY_TOKEN", "")))
        if "console" in names:
            channels.append(ConsoleChannel())
        return channels or [ConsoleChannel()]

    ntfy_topic = os.getenv("NTFY_TOPIC", "").strip()
    if "ntfy" in names and ntfy_topic:
        channels.append(NtfyChannel(
            os.getenv("NTFY_SERVER", "https://ntfy.sh"),
            ntfy_topic,
            os.getenv("NTFY_TOKEN", ""),
        ))
    if "telegram" in names and os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        channels.append(TelegramChannel(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]))
    if "email" in names and os.getenv("SMTP_HOST") and os.getenv("EMAIL_TO"):
        try:
            port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError as exc:
            raise ValueError("SMTP_PORT must be an integer") from exc
        channels.append(EmailChannel(os.environ["SMTP_HOST"], port, os.getenv("SMTP_USER", ""),
                                     os.getenv("SMTP_PASSWORD", ""), os.environ["EMAIL_TO"]))
    if "console" in names:
        channels.append(ConsoleChannel())
    return channels or [ConsoleChannel()]


def notification_status(tenant: str | None = None) -> dict[str, Any]:
    enabled = _truthy("NOTIFY_ENABLED", True)
    channels = [channel.name for channel in _configured_channels(tenant)]
    return {
        "enabled": enabled,
        "channels": channels,
        "ntfy_configured": "ntfy" in channels,
        "min_level": os.getenv("NOTIFY_MIN_LEVEL", "Watch"),
        "interval_minutes": _integer_setting("NOTIFY_INTERVAL_MINUTES", 15, minimum=1),
        "cooldown_hours": _integer_setting("NOTIFY_COOLDOWN_HOURS", 24, minimum=0),
        "dry_run": _truthy("NOTIFY_DRY_RUN", False),
        "last_cycle_at": _RUNTIME["last_cycle_at"],
        "last_result": _RUNTIME["last_result"],
        "notice": "There is no authentication; tenant topics are configuration only, not an access-control boundary.",
    }


def _integer_setting(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA_DIR / "audit.db", timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sent_at TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            tenant_scope TEXT NOT NULL,
            channel TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            days_remaining INTEGER,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            forecast_fingerprint TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notification_state (
            resource_id TEXT NOT NULL,
            tenant_scope TEXT NOT NULL,
            last_risk TEXT NOT NULL,
            last_days INTEGER,
            last_sent_at TEXT,
            last_fingerprint TEXT,
            last_change_key TEXT,
            PRIMARY KEY(resource_id, tenant_scope)
        );
    """)
    connection.commit()
    return connection


def notification_history(tenant: str | None = None, limit: int = 100,
                         resource_id: str | None = None) -> list[dict[str, Any]]:
    with _connect() as connection:
        clauses, params = [], []
        if tenant:
            clauses.append("tenant_scope=?")
            params.append(tenant)
        if resource_id:
            clauses.append("resource_id=?")
            params.append(resource_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = connection.execute(
            "SELECT * FROM notification_log" + where + " ORDER BY id DESC LIMIT ?", params
        )
        return [dict(row) for row in rows.fetchall()]


def _fingerprint(alert: dict[str, Any]) -> str:
    existing = alert.get("forecast_fingerprint")
    if existing:
        return str(existing)
    payload = json.dumps(
        [alert.get("resource"), alert.get("risk_level"), alert.get("lead_time_days"),
         alert.get("predicted_crossing_date"), alert.get("range")],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _change_key(alert: dict[str, Any]) -> str | None:
    changes = alert.get("changes") or []
    return "|".join(f"{change.get('date')}:{change.get('type')}" for change in changes[-3:])


def _alert_body(alert: dict[str, Any], demo: bool = False, event_type: str | None = None) -> str:
    if load_dataset().label == "SYNTHETIC":
        lines = ["SYNTHETIC DATA - advisory only; no facility action is automated."]
    elif demo:
        lines = ["DEMO — advisory only; no facility action is automated."]
    else:
        lines = ["Advisory only — no facility action is automated."]

    resource_type = _resource_type_label(alert.get("resource_type"))
    resource = str(alert.get("resource") or "unknown resource")
    rack = alert.get("rack")
    location = f", Rack {rack}" if rack else ""
    unit = str(alert.get("unit") or "")
    current = alert.get("current_value")
    capacity = alert.get("capacity_limit")
    utilization = (
        float(current) / float(capacity) * 100
        if current is not None and capacity not in (None, 0)
        else None
    )
    if current is not None and capacity is not None:
        usage = f"{fmt(current)} {unit} / {fmt(capacity)} {unit}".strip()
        if utilization is not None:
            usage += f" ({utilization:.0f}% of capacity)"
    elif current is not None:
        usage = f"{fmt(current)} {unit}".strip()
    else:
        usage = "current usage unavailable"
    threshold = alert.get("safety_threshold")
    if threshold is not None:
        usage += f"; safety threshold {fmt(threshold)} {unit}".rstrip()
    lines.append(f"{resource_type} {resource}{location}: {usage}.")

    days = alert.get("lead_time_days")
    crossing_date = _format_date(alert.get("predicted_crossing_date"))
    if days is None:
        estimate = "Time to safety threshold is not reliably estimated"
        note = alert.get("forecast_note")
        if note:
            estimate += f" ({str(note).rstrip('.')})"
        estimate += "."
    else:
        estimate = f"Estimated threshold crossing in about {days} {_plural(days, 'day')}"
        if crossing_date:
            estimate += f" (around {crossing_date})"
        estimate += "."
    low, high = (alert.get("range") or {}).get("low_days"), (alert.get("range") or {}).get("high_days")
    if low is not None and high is not None:
        estimate += f" Forecast range: {low}-{high} days."
    if alert.get("confidence"):
        estimate += f" Confidence: {alert['confidence']}."
    lines.append(estimate)

    event_status = {
        "new": "New alert",
        "ongoing": "Ongoing alert reminder",
        "escalated": "Risk escalated",
        "step_down": "Forecast timing changed",
        "regime_change": "Trend changed",
        "resolved": "Resolved",
        "digest": "Active alert digest",
        "possible_spike_unconfirmed": "Unconfirmed spike",
    }.get(event_type or "", "Alert updated")
    lines.append(f"Status: {event_status}.")

    reasons = []
    threshold = alert.get("safety_threshold")
    if current is not None and threshold is not None and float(current) >= float(threshold):
        reasons.append("current use has reached the operational safety threshold")
    growth = alert.get("weekly_growth")
    if growth is not None and float(growth) > 0:
        reasons.append(f"recent growth {fmt(growth)} {unit}/week".strip())
    changes = alert.get("changes") or []
    if changes:
        latest = changes[-1]
        change_date = _format_date(latest.get("date"))
        reason = str(latest.get("type") or "trend change").replace("_", " ")
        reasons.append(f"{reason}{f' on {change_date}' if change_date else ''}")
    event = next(iter(alert.get("related_events") or []), None)
    if isinstance(event, dict) and event.get("description"):
        reasons.append(f"related event: {event['description']}")
    if reasons:
        lines.append("Reason: " + "; ".join(reasons) + ".")
    elif alert.get("forecast_note"):
        lines.append(f"Reason: {str(alert['forecast_note']).rstrip('.')}.")

    contributor = alert.get("likely_contributor") or {}
    if contributor.get("tenant"):
        lines.append(
            f"Associated workload: {contributor['tenant']} (association only; causation not established)."
        )
    elif contributor.get("wording") and contributor["wording"] != "cause unknown":
        lines.append(f"Attribution: {contributor['wording']}.")

    action = alert.get("recommended_action")
    if action:
        lines.append(f"Recommended action: {str(action).rstrip('.')}.")
    assessed = _format_assessment(alert.get("assessment_timestamp")) or _format_date(alert.get("assessment_date"))
    if assessed:
        lines.append(f"Assessed: {assessed}.")
    return "\n".join(lines)


def _plural(value: int, singular: str) -> str:
    return singular if value == 1 else f"{singular}s"


def _format_date(value: Any) -> str | None:
    if not value or value == "not estimable":
        return None
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%b %d, %Y")
    except ValueError:
        return None


def _format_assessment(value: Any) -> str | None:
    if not value:
        return None
    try:
        assessed_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if assessed_at.tzinfo is None:
        assessed_at = assessed_at.replace(tzinfo=timezone.utc)
    return assessed_at.astimezone(timezone.utc).strftime("%b %d, %Y %H:%M UTC")


def _resource_type_label(value: Any) -> str:
    return {
        "storage": "Storage",
        "network": "Network",
        "power": "Power",
        "thermal": "Cooling/Temperature",
        "vm": "VM",
    }.get(str(value or "").lower(), str(value or "Capacity").title())


def fmt(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _make_message(alert: dict[str, Any], event_type: str, tenant: str | None,
                  demo: bool = False) -> Notification:
    risk = alert.get("risk_level", "Watch")
    resource = str(alert.get("resource", "unknown"))
    resource_type = _resource_type_label(alert.get("resource_type"))
    rack = alert.get("rack")
    location = f" - Rack {rack}" if rack else ""
    days = alert.get("lead_time_days")
    prefix = "[SYNTHETIC] " if load_dataset().label == "SYNTHETIC" else ""
    demo_prefix = "[DEMO] " if demo else ""
    if event_type == "resolved":
        title_status = "RESOLVED Capacity Alert"
    elif event_type == "possible_spike_unconfirmed":
        title_status = "UNCERTAIN Capacity Signal"
    else:
        severity = {"Critical": "CRITICAL", "High Risk": "HIGH RISK", "Watch": "WATCH"}.get(
            str(risk), str(risk).upper()
        )
        title_status = f"{severity} Capacity Risk"
    title = f"{prefix}{demo_prefix}{title_status}: {resource_type}{location}"
    dashboard = os.getenv("DASHBOARD_URL", "http://localhost:5173").rstrip("/")
    query = {
        "resource": resource,
        "alert_id": _alert_id(resource, tenant or "all"),
        "event": event_type,
        "fingerprint": _fingerprint(alert),
    }
    if tenant:
        query["tenant"] = tenant
    click = f"{dashboard}/?{urllib.parse.urlencode(query)}"
    return Notification(
        resource, tenant or "all", risk, days, event_type, title, _alert_body(alert, demo, event_type),
        {"Critical": "urgent", "High Risk": "high", "Watch": "default"}.get(risk, "low"),
        "warning", click, _fingerprint(alert),
    )


def _alert_id(resource: str, scope: str) -> str:
    return hashlib.sha256(f"{scope}:{resource}".encode("utf-8")).hexdigest()[:16]


def _digest_line(alert: dict[str, Any]) -> str:
    resource_type = _resource_type_label(alert.get("resource_type"))
    rack = f", Rack {alert['rack']}" if alert.get("rack") else ""
    unit = str(alert.get("unit") or "")
    current, capacity, threshold = (
        alert.get("current_value"),
        alert.get("capacity_limit"),
        alert.get("safety_threshold"),
    )
    usage = f"{fmt(current)} {unit}".strip() if current is not None else "usage unavailable"
    if current is not None and capacity is not None:
        usage += f" / {fmt(capacity)} {unit}".rstrip()
        if float(capacity):
            usage += f" ({float(current) / float(capacity) * 100:.0f}%)"
    if threshold is not None:
        usage += f"; safety threshold {fmt(threshold)} {unit}".rstrip()
    days = alert.get("lead_time_days")
    if days is None:
        estimate = "time not estimated"
    else:
        estimate = f"~{days} {_plural(days, 'day')}"
        date_text = _format_date(alert.get("predicted_crossing_date"))
        if date_text:
            estimate += f", {date_text}"
    growth = alert.get("weekly_growth")
    reason = f"; growth {fmt(growth)} {unit}/week" if growth is not None and float(growth) > 0 else ""
    return (
        f"- {str(alert.get('risk_level', 'Watch')).upper()} {resource_type} "
        f"{alert.get('resource', 'unknown')}{rack}: {usage}; {estimate}{reason}."
    )


def _write_log(connection: sqlite3.Connection, message: Notification, channel: str,
               status: str, error: str | None = None) -> None:
    connection.execute(
        """INSERT INTO notification_log
        (sent_at, resource_id, tenant_scope, channel, risk_level, days_remaining, event_type,
         title, status, error, forecast_fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), message.resource_id, message.tenant_scope,
         channel, message.risk_level, message.days_remaining, message.event_type,
         message.title, status, error, message.forecast_fingerprint),
    )


def _store_state(connection: sqlite3.Connection, alert: dict[str, Any], state_scope: str,
                 sent_at: str | None, fingerprint: str, change_key: str | None) -> None:
    connection.execute(
        """INSERT INTO notification_state
        (resource_id, tenant_scope, last_risk, last_days, last_sent_at, last_fingerprint, last_change_key)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(resource_id, tenant_scope) DO UPDATE SET
        last_risk=excluded.last_risk, last_days=excluded.last_days,
        last_sent_at=CASE WHEN excluded.last_risk IN ('Healthy', 'Insufficient Data')
                          THEN NULL ELSE COALESCE(excluded.last_sent_at, notification_state.last_sent_at) END,
        last_fingerprint=excluded.last_fingerprint, last_change_key=excluded.last_change_key""",
        (str(alert["resource"]), state_scope, str(alert.get("risk_level", "Healthy")),
         alert.get("lead_time_days"), sent_at, fingerprint, change_key),
    )


def _decision(alert: dict[str, Any], previous: sqlite3.Row | None,
              min_level: str, send_resolved: bool, send_unconfirmed: bool) -> str | None:
    risk = str(alert.get("risk_level", "Healthy"))
    current_rank = LEVELS.get(risk, 0)
    previous_rank = LEVELS.get(previous["last_risk"], 0) if previous else 0
    alert_type = alert.get("alert_type")
    if alert_type == "possible_spike_unconfirmed":
        return "possible_spike_unconfirmed" if send_unconfirmed else None
    if current_rank == 0:
        return "resolved" if previous_rank > 0 and send_resolved else None
    if current_rank < LEVELS.get(min_level, LEVELS["Watch"]):
        return None
    if previous_rank == 0:
        return "new"
    if current_rank > previous_rank:
        return "escalated"
    old_days, new_days = previous["last_days"], alert.get("lead_time_days")
    if old_days is not None and new_days is not None:
        for threshold in STEP_DAYS:
            if old_days > threshold >= new_days:
                return "step_down"
    old_change = previous["last_change_key"]
    new_change = _change_key(alert)
    if (new_change and new_change != old_change and old_days is not None and new_days is not None
            and abs(old_days - new_days) >= max(7, old_days * .2)):
        return "regime_change"
    return "ongoing"


def _dispatch_one(message: Notification, channels: list[NotificationChannel],
                  dry_run: bool) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for channel in channels:
        if dry_run:
            print(f"[DRY RUN:{channel.name}] {message.title}\n{message.body}\n{message.click}", flush=True)
            results.append((channel.name, "dry_run"))
            continue
        failure_kind = "delivery failed"
        for attempt in range(2):
            try:
                channel.send(message.title, message.body, message.priority, message.tags, message.click)
                results.append((channel.name, "sent"))
                break
            except Exception as exc:
                if attempt == 0:
                    time.sleep(.1)
                else:
                    LOGGER.error("Notification delivery failed via %s (%s)", channel.name, type(exc).__name__)
                    results.append((channel.name, "failed"))
    return results


def dispatch(
    alerts: list[dict[str, Any]],
    tenant: str | None = None,
    dry_run: bool | None = None,
    state_scope: str | None = None,
    channel_overrides: list[NotificationChannel] | None = None,
    demo: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    started = now or datetime.now(timezone.utc)
    scope = state_scope or tenant or "all"
    is_dry_run = _truthy("NOTIFY_DRY_RUN", False) if dry_run is None else dry_run
    enabled = _truthy("NOTIFY_ENABLED", True)
    min_level = os.getenv("NOTIFY_MIN_LEVEL", "Watch")
    if min_level not in LEVELS:
        raise ValueError("NOTIFY_MIN_LEVEL must be Watch, High Risk, or Critical")
    cooldown = _integer_setting("NOTIFY_COOLDOWN_HOURS", 24, minimum=0)
    send_resolved = _truthy("NOTIFY_SEND_RESOLVED", False)
    send_unconfirmed = _truthy("NOTIFY_SEND_UNCONFIRMED", False)
    channels = channel_overrides or _configured_channels(tenant)
    normalized_alerts = []
    for alert in alerts:
        if tenant:
            # Callers must provide an already scoped forecast; this guard prevents obvious mixed input.
            if alert.get("tenant_scope") not in (None, tenant):
                continue
        normalized_alerts.append(alert)

    eligible: list[tuple[dict[str, Any], str, str, str | None]] = []
    connection = _connect()
    try:
        for alert in normalized_alerts:
            resource = str(alert["resource"])
            fingerprint = _fingerprint(alert)
            change_key = _change_key(alert)
            previous = connection.execute(
                "SELECT * FROM notification_state WHERE resource_id=? AND tenant_scope=?",
                (resource, scope),
            ).fetchone()
            event_type = _decision(alert, previous, min_level, send_resolved, send_unconfirmed)
            current_rank = LEVELS.get(str(alert.get("risk_level", "Healthy")), 0)
            if event_type:
                last_sent = datetime.fromisoformat(previous["last_sent_at"]) if previous and previous["last_sent_at"] else None
                escalated = event_type in ("escalated", "step_down", "regime_change", "resolved")
                in_cooldown = last_sent is not None and started - last_sent < timedelta(hours=cooldown)
                if in_cooldown and not escalated:
                    skipped = _make_message(alert, event_type, tenant, demo)
                    for channel in channels:
                        _write_log(connection, skipped, channel.name, "skipped_cooldown")
                    event_type = None
                elif enabled:
                    eligible.append((alert, event_type, fingerprint, change_key))
                else:
                    skipped = _make_message(alert, event_type, tenant, demo)
                    for channel in channels:
                        _write_log(connection, skipped, channel.name, "skipped_cooldown", "notifications disabled")
                    event_type = None
            sent_at = None
            if event_type is None and current_rank == 0 and previous and previous["last_risk"] != "Healthy":
                # Preserve the last delivery time while remembering the recovery for future transitions.
                pass
            _store_state(connection, alert, scope, sent_at, fingerprint, change_key)
        connection.commit()

        # Send one digest for a batch, then direct messages only for Critical resources.
        eligible.sort(key=lambda item: (-LEVELS.get(str(item[0].get("risk_level")), 0),
                                        item[0].get("lead_time_days") if item[0].get("lead_time_days") is not None else 10**9))
        messages: list[tuple[Notification, list[dict[str, Any]], str]] = []
        if len(eligible) > 1:
            digest_items = eligible[:5]
            body_lines = ["Advisory-only capacity changes:"]
            for alert, event_type, _, _ in digest_items:
                body_lines.append(_digest_line(alert))
            body_lines.append("Open the dashboard for evidence and recommendations.")
            if demo:
                body_lines.insert(0, "DEMO (synthetic data)")
            top = digest_items[0][0]
            digest_alert = dict(top)
            digest_alert["resource"] = "Capacity alert digest"
            digest_alert["lead_time_days"] = None
            digest_alert["tenant_scope"] = scope
            digest_fingerprint = hashlib.sha256(
                "|".join(item[2] for item in digest_items).encode()
            ).hexdigest()[:16]
            digest_message = _make_message(digest_alert, "digest", tenant, demo)
            digest_message = Notification(
                "digest", tenant or "all", top.get("risk_level", "Watch"), None, "digest",
                ("[SYNTHETIC] " if load_dataset().label == "SYNTHETIC" else "") +
                ("[DEMO] " if demo else "") + "[Capacity] Alert digest",
                "\n".join(body_lines),
                {"Critical": "urgent", "High Risk": "high", "Watch": "default"}.get(top.get("risk_level"), "default"),
                "warning", digest_message.click, digest_fingerprint,
            )
            messages.append((digest_message, [item[0] for item in digest_items], "digest"))
            for alert, event_type, _, _ in eligible:
                if alert.get("risk_level") == "Critical":
                    messages.append((_make_message(alert, event_type, tenant, demo), [alert], event_type))
        else:
            for alert, event_type, _, _ in eligible:
                messages.append((_make_message(alert, event_type, tenant, demo), [alert], event_type))

        sent_resources: set[str] = set()
        outcomes = []
        for message, represented_alerts, message_event_type in messages:
            results = _dispatch_one(message, channels, is_dry_run)
            outcomes.extend((channel, status) for channel, status in results)
            for alert in represented_alerts:
                fingerprint = _fingerprint(alert)
                for channel_name, status in results:
                    logged_message = Notification(
                        str(alert["resource"]), tenant or "all", str(alert.get("risk_level", "Watch")),
                        alert.get("lead_time_days"), message_event_type,
                        message.title, message.body, message.priority, message.tags, message.click, fingerprint,
                    )
                    _write_log(connection, logged_message, channel_name, status,
                               None if status != "failed" else "delivery failed")
                if any(status in ("sent", "dry_run") for _, status in results):
                    sent_resources.add(str(alert["resource"]))
        delivered_at = started.isoformat()
        if sent_resources:
            for resource in sent_resources:
                connection.execute(
                    """UPDATE notification_state SET last_sent_at=?
                    WHERE resource_id=? AND tenant_scope=?
                    AND last_risk NOT IN ('Healthy', 'Insufficient Data')""",
                    (delivered_at, resource, scope),
                )
        connection.commit()
        result = {
            "enabled": enabled,
            "dry_run": is_dry_run,
            "alerts_evaluated": len(normalized_alerts),
            "notifications_eligible": len(eligible),
            "messages_dispatched": len(messages),
            "channels": [channel.name for channel in channels],
            "sent": sum(status == "sent" for _, status in outcomes),
            "dry_run_messages": sum(status == "dry_run" for _, status in outcomes),
            "failed": sum(status == "failed" for _, status in outcomes),
        }
        return result
    finally:
        connection.close()


def run_notification_cycle(
    tenant: str | None = None,
    as_of: str | None = None,
    dry_run: bool | None = None,
    state_scope: str | None = None,
    channel_overrides: list[NotificationChannel] | None = None,
    demo: bool = False,
    alerts_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not _CYCLE_LOCK.acquire(blocking=False):
        return {"skipped": True, "reason": "notification cycle already running"}
    try:
        if not _truthy("NOTIFY_ENABLED", True) and alerts_override is None:
            result = {"enabled": False, "notifications_eligible": 0, "messages_dispatched": 0}
        else:
            if alerts_override is None:
                from backend import app as api

                dataset = api._tenant_dataset(tenant)
                alerts = []
                for resource_id in sorted(dataset.telemetry.resource_id.unique()):
                    output = api._make_forecast(str(resource_id), tenant, as_of)
                    output["resource"] = api._inventory_row(str(resource_id))
                    alert = api._alert(str(resource_id), output)
                    alert.update({
                        "current_value": output.get("current_value"),
                        "safety_threshold": output.get("safety_threshold"),
                        "utilisation_pct": output.get("utilisation_pct"),
                        "confidence": output.get("confidence"),
                        "changes": output.get("changes", []),
                        "forecast_fingerprint": api._write_audit_and_fingerprint(
                            str(resource_id), tenant, output, dataset
                        ),
                        "tenant_scope": tenant or "all",
                    })
                    alerts.append(alert)
            else:
                alerts = alerts_override
            result = dispatch(
                alerts, tenant=tenant, dry_run=dry_run, state_scope=state_scope,
                channel_overrides=channel_overrides, demo=demo,
            )
        _RUNTIME["last_cycle_at"] = datetime.now(timezone.utc).isoformat()
        _RUNTIME["last_result"] = result
        return result
    except Exception:
        LOGGER.exception("Notification cycle failed; the API remains available")
        result = {"enabled": _truthy("NOTIFY_ENABLED", True), "error": "notification cycle failed"}
        _RUNTIME["last_cycle_at"] = datetime.now(timezone.utc).isoformat()
        _RUNTIME["last_result"] = result
        return result
    finally:
        _CYCLE_LOCK.release()


async def notification_loop() -> None:
    await asyncio.sleep(1)
    while True:
        await asyncio.to_thread(run_notification_cycle)
        await asyncio.sleep(_integer_setting("NOTIFY_INTERVAL_MINUTES", 15, minimum=1) * 60)


def send_test_notification(
    channels: list[NotificationChannel] | None = None,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    label = "[SYNTHETIC] " if load_dataset().label == "SYNTHETIC" else ""
    message = Notification(
        "test", "all", "Healthy", None, "test",
        f"{label}[TEST] Capacity Intelligence mobile notification",
        "TEST notification. Advisory only; no facility action is performed.",
        "default", "white_check_mark", os.getenv("DASHBOARD_URL", "http://localhost:5173"),
        hashlib.sha256(b"capacity-intelligence-notification-test").hexdigest()[:16],
    )
    selected_channels = channels or _configured_channels()
    is_dry = _truthy("NOTIFY_DRY_RUN", False) if dry_run is None else dry_run
    results = _dispatch_one(message, selected_channels, is_dry)
    with _connect() as connection:
        for name, status in results:
            _write_log(connection, message, name, status, None if status != "failed" else "delivery failed")
    return {"channels": [channel.name for channel in selected_channels],
            "sent": sum(status == "sent" for _, status in results),
            "dry_run": sum(status == "dry_run" for _, status in results),
            "failed": sum(status == "failed" for _, status in results)}


def _simulate_escalation() -> dict[str, Any]:
    from backend import app as api

    dataset = load_dataset()
    latest = datetime.fromisoformat(dataset.last_timestamp).date()
    candidates: list[tuple[int, int, str, date]] = []
    for resource_id in sorted(dataset.telemetry.resource_id.unique()):
        record = api._inventory_row(str(resource_id))
        current = api._make_forecast(str(resource_id))
        current_rank = LEVELS.get(current["status"], 0)
        if current_rank < LEVELS["High Risk"]:
            continue
        for days_back in range(15, 121, 7):
            earlier_date = latest - timedelta(days=days_back)
            earlier = api._make_forecast(str(resource_id), as_of=earlier_date.isoformat())
            earlier_rank = LEVELS.get(earlier["status"], 0)
            if 0 < earlier_rank < current_rank:
                candidates.append((current_rank - earlier_rank, days_back, str(resource_id), earlier_date))
                break
    if not candidates:
        result = run_notification_cycle(dry_run=True, state_scope="demo", demo=True)
        result["demo_note"] = "No historical risk escalation was found in the synthetic history."
        return result
    _, _, resource_id, earlier_date = max(candidates)
    record = api._inventory_row(resource_id)
    initial = api._make_forecast(resource_id, as_of=earlier_date.isoformat())
    initial_alert = api._alert(resource_id, {**initial, "resource": record})
    initial_alert.update({"current_value": initial.get("current_value"),
                          "safety_threshold": initial.get("safety_threshold"),
                          "utilisation_pct": initial.get("utilisation_pct"),
                          "confidence": initial.get("confidence"),
                          "changes": initial.get("changes", []),
                          "forecast_fingerprint": api._write_audit_and_fingerprint(
                              resource_id, None, initial, api.DATASET
                          )})
    first = run_notification_cycle(
        as_of=earlier_date.isoformat(), dry_run=True, state_scope="demo", demo=True,
        alerts_override=[initial_alert],
    )
    current = api._make_forecast(resource_id)
    latest_alert = api._alert(resource_id, {**current, "resource": record})
    latest_alert.update({"current_value": current.get("current_value"),
                         "safety_threshold": current.get("safety_threshold"),
                         "utilisation_pct": current.get("utilisation_pct"),
                         "confidence": current.get("confidence"),
                         "changes": current.get("changes", []),
                         "forecast_fingerprint": api._write_audit_and_fingerprint(
                             resource_id, None, current, api.DATASET
                         )})
    second = run_notification_cycle(
        dry_run=True, state_scope="demo", demo=True, alerts_override=[latest_alert],
    )
    return {"resource": resource_id, "earlier_date": earlier_date.isoformat(),
            "earlier": first, "latest": second}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capacity Intelligence mobile notification tools")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--test", action="store_true", help="send a labelled test notification")
    modes.add_argument("--run-once", action="store_true", help="evaluate current alerts immediately")
    modes.add_argument("--dry-run", action="store_true", help="evaluate and print without sending")
    modes.add_argument("--simulate-escalation", action="store_true", help="replay a synthetic risk escalation")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.test:
        result = send_test_notification()
    elif args.run_once:
        result = run_notification_cycle()
    elif args.dry_run:
        result = run_notification_cycle(dry_run=True)
    else:
        result = _simulate_escalation()
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

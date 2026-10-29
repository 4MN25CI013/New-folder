from __future__ import annotations

import hashlib
import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from backend.data import DATA_DIR, Dataset, load_dataset, resource_series
from backend.engine import ENGINE_VERSION, forecast
from backend.validate import run_validation
from backend.mobile import (
    get_connected_mobile_storage,
    trigger_mobile_popup,
    get_modal_html,
    get_adb_path,
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_audit_batch()
    from backend.notify import notification_loop

    notification_task = asyncio.create_task(notification_loop())
    try:
        yield
    finally:
        notification_task.cancel()
        try:
            await notification_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Capacity Intelligence", description=(
    "Advisory-only, read-only capacity forecasting prototype. There is no authentication; "
    "the tenant parameter simulates a tenant session and is not production-secure."
), lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

DATASET = load_dataset()
AUDIT_PATH = DATA_DIR / "audit.db"
AUDIT_FIELDS = (
    "id", "run_at", "resource_id", "tenant_scope", "model", "engine_version", "data_start",
    "data_end", "input_fingerprint", "status", "days_remaining", "result_json",
)


def _connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(AUDIT_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("""
        CREATE TABLE IF NOT EXISTS forecast_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            tenant_scope TEXT NOT NULL,
            model TEXT,
            engine_version TEXT NOT NULL,
            data_start TEXT,
            data_end TEXT,
            input_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            days_remaining INTEGER,
            result_json TEXT NOT NULL
        )
    """)
    connection.commit()
    return connection


def _tenant_dataset(tenant: str | None) -> Dataset:
    if tenant is None:
        return DATASET
    if tenant not in DATASET.tenants:
        raise HTTPException(status_code=404, detail="Tenant not found")
    telemetry = DATASET.telemetry.loc[DATASET.telemetry.tenant_id == tenant].copy()
    events = DATASET.events.loc[DATASET.events.tenant_id == tenant].copy()
    return Dataset(telemetry, DATASET.inventory, events, [tenant], DATASET.label, DATASET.counts,
                   DATASET.last_timestamp, DATASET.status)


def _resource_frame(dataset: Dataset, resource_id: str, as_of: str | None = None) -> pd.DataFrame:
    frame = resource_series(dataset, resource_id, as_of=as_of)
    if frame.empty:
        raise HTTPException(status_code=404, detail="Resource not found")
    return frame


def _events_for(dataset: Dataset, resource_id: str) -> list[dict[str, Any]]:
    selected = dataset.events.loc[dataset.events.resource_id == resource_id]
    return selected.to_dict(orient="records")


def _tenant_values(dataset: Dataset, resource_id: str, as_of: str | None = None) -> dict[str, pd.Series]:
    result = {}
    selected = dataset.telemetry.loc[dataset.telemetry.resource_id == resource_id]
    if as_of is not None:
        cutoff = pd.Timestamp(as_of).normalize()
        selected = selected.loc[pd.to_datetime(selected.timestamp) <= cutoff].copy()
        if "imputed_until" in selected:
            later_imputation = pd.to_datetime(selected.imputed_until, errors="coerce") > cutoff
            selected.loc[later_imputation, "value"] = float("nan")
    for tenant, group in selected.groupby("tenant_id"):
        series = group.set_index(pd.to_datetime(group.timestamp)).value.dropna().sort_index()
        result[str(tenant)] = series
    return result


def _make_forecast(resource_id: str, tenant: str | None = None, as_of: str | None = None) -> dict[str, Any]:
    dataset = _tenant_dataset(tenant)
    frame = _resource_frame(dataset, resource_id, as_of)
    record = dataset.inventory.loc[dataset.inventory.resource_id == resource_id]
    if record.empty:
        raise HTTPException(status_code=404, detail="Resource not found")
    events = _events_for(dataset, resource_id)
    output = forecast(frame, record.iloc[0].to_dict(), _tenant_values(dataset, resource_id, as_of), events, as_of=as_of)
    output["resource"] = record.iloc[0].to_dict()
    return output


def _write_audit(resource_id: str, tenant: str | None, result: dict[str, Any], dataset: Dataset) -> str:
    frame = _resource_frame(dataset, resource_id, result.get("as_of"))
    values = frame.loc[pd.to_datetime(frame.timestamp).dt.date <= pd.Timestamp(result.get("as_of", dataset.last_timestamp)).date()]
    fingerprint_payload = values[["timestamp", "value"]].to_json(orient="values", date_format="iso")
    fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()[:16]
    with closing(_connection()) as connection:
        connection.execute(
            """INSERT INTO forecast_runs
            (run_at, resource_id, tenant_scope, model, engine_version, data_start, data_end,
             input_fingerprint, status, days_remaining, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(), resource_id, tenant or "all",
                result.get("model"), ENGINE_VERSION, str(values.timestamp.min()) if not values.empty else None,
                str(values.timestamp.max()) if not values.empty else None, fingerprint,
                result.get("status", "unknown"), result.get("days_remaining"),
                json.dumps(result, default=str, sort_keys=True),
            ),
        )
        connection.commit()
    return fingerprint


def _write_audit_and_fingerprint(
    resource_id: str, tenant: str | None, result: dict[str, Any], dataset: Dataset
) -> str:
    return _write_audit(resource_id, tenant, result, dataset)


@lru_cache(maxsize=5)
def _validation(tenant: str | None) -> dict[str, Any]:
    dataset = _tenant_dataset(tenant)
    return run_validation(dataset)


def _inventory_row(resource_id: str) -> dict[str, Any]:
    row = DATASET.inventory.loc[DATASET.inventory.resource_id == resource_id]
    return row.iloc[0].to_dict()


def _recommendation(resource_type: str) -> str:
    return {
        "storage": "Review retention and backup policies and cleanup opportunities; begin capacity planning given the 6-10 week hardware lead time.",
        "network": "Investigate sustained bandwidth growth, rebalance workloads, and review uplink headroom.",
        "power": "Review rack demand growth and PDU/circuit headroom.",
        "thermal": "Review the thermal trend against the safety margin, including airflow, containment, and equipment placement.",
        "vm": "Evaluate workload allocation and licence headroom.",
    }.get(resource_type, "Review the trend and available operational headroom.")


def _summary_rows(tenant: str | None) -> list[dict[str, Any]]:
    dataset = _tenant_dataset(tenant)
    ids = sorted(dataset.telemetry.resource_id.unique())
    return [{"resource": _inventory_row(resource_id), **_make_forecast(resource_id, tenant)}
            for resource_id in ids]


def _alert(resource_id: str, output: dict[str, Any]) -> dict[str, Any]:
    resource = output["resource"]
    changes = output.get("changes", [])
    spikes = output.get("spikes", [])
    if any(item["type"] == "unconfirmed_spike" for item in spikes):
        alert_type = "possible_spike_unconfirmed"
    elif any(
        item["type"] in ("growth_rate_change", "level_shift")
        and (pd.Timestamp(output.get("as_of")) - pd.Timestamp(item["date"])).days <= 60
        for item in changes
    ):
        alert_type = "regime_change"
    else:
        alert_type = "sustained_growth"
    contributor = output.get("attribution", [{"wording": "cause unknown"}])[0]
    evidence = [
        f"Current use {output.get('current_value', 0):.2f} {resource['unit']} ({output.get('utilisation_pct', 0):.1f}% of safety threshold {resource['safety_threshold']}; hard limit {resource['capacity_limit']} {resource['unit']}).",
        f"Recent fitted growth: {output.get('weekly_growth', 0):.3f} {resource['unit']}/week.",
        f"Forecast method: {output.get('model', 'unavailable')}; 14-day holdout MAE — OLS {output.get('method_scores', {}).get('ols_mae')}, damped Holt {output.get('method_scores', {}).get('holt_mae')}.",
    ]
    evidence.extend(f"Detected {change['type']} on {change['date']}." for change in changes[-3:])
    lead = output.get("days_remaining")
    return {
        "resource": resource["resource_id"], "resource_type": resource["resource_type"],
        "unit": resource["unit"], "capacity_limit": resource["capacity_limit"],
        "rack": resource["rack_id"], "risk_level": output["status"],
        "alert_type": alert_type, "predicted_crossing_date": output.get("crossing_date") or "not estimable",
        "lead_time_days": lead, "range": output.get("range"), "confidence": output.get("confidence"),
        "current_value": output.get("current_value"), "safety_threshold": resource["safety_threshold"],
        "utilisation_pct": output.get("utilisation_pct"), "weekly_growth": output.get("weekly_growth"),
        "assessment_date": output.get("as_of"),
        "assessment_timestamp": datetime.now(timezone.utc).isoformat(),
        "forecast_note": output.get("note"),
        "changes": changes,
        "evidence": evidence, "related_events": output.get("events", []),
        "likely_contributor": contributor, "recommended_action": _recommendation(resource["resource_type"]),
    }


def startup_audit_batch() -> None:
    for resource_id in sorted(DATASET.telemetry.resource_id.unique()):
        result = _make_forecast(resource_id)
        _write_audit(resource_id, None, result, DATASET)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "read_only": True, "auth": "not implemented - not production-secure",
            "notice": "There is no authentication; tenant selection only simulates a tenant session."}


@app.get("/api/dataset")
def dataset_info() -> dict[str, Any]:
    return {"label": DATASET.label, "last_timestamp": DATASET.last_timestamp,
            "rows_dropped": DATASET.counts, "validation_status": DATASET.status,
            "synthetic_label": "SYNTHETIC — no facility measurements" if DATASET.label == "SYNTHETIC" else None}


@app.get("/api/summary")
def summary(tenant: str | None = None) -> dict[str, Any]:
    outputs = _summary_rows(tenant)
    counts = {name: sum(row["status"] == name for row in outputs)
              for name in ("Critical", "High Risk", "Watch", "Healthy", "Insufficient Data")}
    days = [row.get("days_remaining") for row in outputs if row.get("days_remaining") is not None]
    validation = _validation(tenant)
    warning = validation["warning_evaluation"]
    return {
        "total_resources": len(outputs), "at_risk": sum(row["status"] in ("Critical", "High Risk", "Watch") for row in outputs),
        "within_7_days": sum(d <= 7 for d in days), "within_30_days": sum(d <= 30 for d in days),
        "within_90_days": sum(d <= 90 for d in days), "counts_by_status": counts,
        "validation": validation, "false_alarm_rate": warning["false_alarm_rate"],
        "median_warning_lead_time_days": warning["median_warning_lead_time_days"], "dataset": dataset_info(),
    }


@app.get("/api/resources")
def resources(
    tenant: str | None = None, resource_type: str | None = None, rack: str | None = None,
    risk: Literal["Critical", "High Risk", "Watch", "Healthy", "Insufficient Data"] | None = None,
    q: str | None = None, sort: Literal["risk", "days", "utilisation", "growth"] = "risk",
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    _tenant_dataset(tenant)
    outputs = _summary_rows(tenant)
    risk_order = {"Critical": 0, "High Risk": 1, "Watch": 2, "Insufficient Data": 3, "Healthy": 4}
    filtered = []
    for output in outputs:
        item = output["resource"]
        if resource_type and item["resource_type"] != resource_type:
            continue
        if rack and item["rack_id"] != rack:
            continue
        if risk and output["status"].lower() != risk.lower():
            continue
        if q and q.lower() not in item["resource_id"].lower():
            continue
        filtered.append({
            **item, "status": output["status"], "current_value": output.get("current_value"),
            "utilisation_pct": output.get("utilisation_pct"), "weekly_growth": output.get("weekly_growth"),
            "days_remaining": output.get("days_remaining"), "confidence": output.get("confidence"),
        })
    if sort == "days":
        filtered.sort(key=lambda x: (x["days_remaining"] is None, x["days_remaining"] or 0))
    elif sort == "utilisation":
        filtered.sort(key=lambda x: x["utilisation_pct"] or 0, reverse=True)
    elif sort == "growth":
        filtered.sort(key=lambda x: x["weekly_growth"] or 0, reverse=True)
    else:
        filtered.sort(key=lambda x: (risk_order.get(x["status"], 5), x["days_remaining"] is None,
                                     x["days_remaining"] or 0))
    return {"items": filtered[offset:offset + limit], "total": len(filtered), "limit": limit, "offset": offset}


@app.get("/api/resources/{resource_id}/forecast")
def resource_forecast(resource_id: str, tenant: str | None = None) -> dict[str, Any]:
    dataset = _tenant_dataset(tenant)
    result = _make_forecast(resource_id, tenant)
    _write_audit(resource_id, tenant, result, dataset)
    result["alert"] = _alert(resource_id, result)
    result["audit_history"] = _audit_rows(tenant, resource_id, 10)
    return result


@app.get("/api/alerts")
def alerts(tenant: str | None = None, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    outputs = _summary_rows(tenant)
    alerts_list = []
    for output in outputs:
        if output["status"] in ("Critical", "High Risk", "Watch"):
            alerts_list.append(_alert(output["resource"]["resource_id"], output))
        elif any(spike["type"] == "unconfirmed_spike" for spike in output.get("spikes", [])):
            alerts_list.append(_alert(output["resource"]["resource_id"], output))
    order = {"Critical": 0, "High Risk": 1, "Watch": 2, "Healthy": 3, "Insufficient Data": 4}
    alerts_list.sort(key=lambda a: (order.get(a["risk_level"], 5),
                                    a["lead_time_days"] if a["lead_time_days"] is not None else float("inf")))
    return {"items": alerts_list[:limit], "total": len(alerts_list)}


@app.get("/api/events")
def events(tenant: str | None = None, resource_id: str | None = None) -> dict[str, Any]:
    dataset = _tenant_dataset(tenant)
    selected = dataset.events
    if resource_id:
        selected = selected.loc[selected.resource_id == resource_id]
        if tenant and selected.empty and not (dataset.telemetry.resource_id == resource_id).any():
            raise HTTPException(status_code=404, detail="Resource not found")
    return {"items": selected.to_dict(orient="records")}


def _audit_rows(tenant: str | None, resource_id: str | None, limit: int) -> list[dict[str, Any]]:
    with closing(_connection()) as connection:
        query = "SELECT * FROM forecast_runs"
        clauses, params = [], []
        if tenant:
            clauses.append("tenant_scope=?")
            params.append(tenant)
        if resource_id:
            clauses.append("resource_id=?")
            params.append(resource_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in connection.execute(query, params).fetchall()]


@app.get("/api/audit")
def audit(tenant: str | None = None, resource_id: str | None = None,
          limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    _tenant_dataset(tenant)
    if tenant and resource_id:
        _resource_frame(_tenant_dataset(tenant), resource_id)
    return {"items": _audit_rows(tenant, resource_id, limit)}


@app.get("/api/validation")
def validation(tenant: str | None = None) -> dict[str, Any]:
    _tenant_dataset(tenant)
    return _validation(tenant)


@app.get("/api/tenants")
def tenants() -> dict[str, Any]:
    return {"items": DATASET.tenants, "notice": "Demo tenant list; there is no authentication."}


@app.get("/api/notifications/status")
def notifications_status(tenant: str | None = None) -> dict[str, Any]:
    _tenant_dataset(tenant)
    from backend.notify import notification_status

    return notification_status(tenant=tenant)


@app.get("/api/notifications")
def notifications(tenant: str | None = None, limit: int = Query(100, ge=1, le=500),
                  resource_id: str | None = None) -> dict[str, Any]:
    _tenant_dataset(tenant)
    if tenant and resource_id:
        _resource_frame(_tenant_dataset(tenant), resource_id)
    from backend.notify import notification_history

    return {"items": notification_history(tenant=tenant, limit=limit, resource_id=resource_id)}


@app.get("/api/mobile/status")
def mobile_status() -> dict[str, Any]:
    return get_connected_mobile_storage()


@app.post("/api/mobile/trigger-alert")
def mobile_trigger() -> dict[str, Any]:
    return trigger_mobile_popup()


@app.get("/api/mobile/modal", response_class=HTMLResponse)
def mobile_modal() -> HTMLResponse:
    data = get_connected_mobile_storage()
    return HTMLResponse(content=get_modal_html(data), status_code=200)


@app.get("/api/mobile/open-storage", response_class=HTMLResponse)
def mobile_open_storage() -> HTMLResponse:
    adb = get_adb_path()
    if adb:
        import subprocess
        subprocess.run([adb, "shell", "am", "start", "-a", "android.settings.INTERNAL_STORAGE_SETTINGS"], capture_output=True)
    return HTMLResponse(
        content="<html><body style='background:#111;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;'>"
        "<script>window.close();</script><h3>Opening Storage Settings...</h3></body></html>",
        status_code=200,
    )


dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

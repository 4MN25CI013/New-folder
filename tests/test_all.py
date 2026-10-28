from __future__ import annotations

from datetime import date, timedelta
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.data import generate_synthetic_files, load_dataset, resource_series
from backend.engine import forecast


def history(values: np.ndarray) -> pd.DataFrame:
    start = date(2025, 1, 1)
    return pd.DataFrame({
        "timestamp": [(start + timedelta(days=i)).isoformat() for i in range(len(values))],
        "value": values,
    })


def inventory(threshold: float = 150) -> dict:
    return {"safety_threshold": threshold, "capacity_limit": threshold + 20, "unit": "units"}


def test_sustained_growth_crossing_and_interval():
    result = forecast(history(np.arange(10.0, 110.0)), inventory(150))
    expected_days = 41
    assert result["days_remaining"] == pytest.approx(expected_days, rel=.25)
    assert result["range"]["low_days"] <= result["days_remaining"] <= result["range"]["high_days"]


def test_flat_series_has_no_exhaustion_estimate():
    result = forecast(history(np.full(100, 25.0)), inventory())
    assert result["days_remaining"] is None
    assert result["status"] == "Healthy"
    assert result["note"] == "no statistically supported growth; exhaustion date not estimated"


def test_crossing_beyond_projection_horizon_is_not_estimated():
    result = forecast(history(50 + .01 * np.arange(100)), inventory(200))
    assert result["days_remaining"] is None
    assert "beyond the 365-day horizon" in result["note"]


def test_temporary_spike_is_detected_and_does_not_raise_status():
    values = np.full(100, 25.0)
    values[70] = 100
    result = forecast(history(values), inventory())
    assert any(spike["type"] in ("temporary_spike", "unconfirmed_spike") for spike in result["spikes"])
    assert result["status"] == "Healthy"
    assert result["days_remaining"] is None


def test_growth_change_is_detected_with_previous_forecast():
    values = np.r_[10 + .05 * np.arange(60), 13 + .8 * np.arange(40)]
    result = forecast(history(values), inventory(200))
    assert any(change["type"] == "growth_rate_change" for change in result["changes"])
    assert result["previous_forecast"] is not None


def test_insufficient_observations_are_flagged():
    result = forecast(history(np.arange(29.0)), inventory())
    assert result["status"] == "Insufficient Data"
    assert result["days_remaining"] is None


def test_as_of_excludes_all_later_values():
    values = np.linspace(20, 70, 100)
    baseline = history(values)
    changed = history(values.copy())
    changed.loc[80:, "value"] = 10000
    first = forecast(baseline, inventory(120), as_of="2025-03-10")
    second = forecast(changed, inventory(120), as_of="2025-03-10")
    assert first["current_value"] == second["current_value"]
    assert first["days_remaining"] == second["days_remaining"]
    assert first["model"] == second["model"]


def test_loader_counts_and_interpolates_injected_quality_defects(tmp_path):
    generate_synthetic_files(tmp_path)
    dataset = load_dataset(tmp_path)
    assert dataset.label == "SYNTHETIC"
    assert dataset.counts["duplicates_dropped"] == 1
    assert dataset.counts["invalid_values_dropped"] == 1
    assert dataset.counts["missing_values_interpolated"] >= 2
    assert not (dataset.telemetry.value < 0).any()


def test_long_gaps_are_not_partially_interpolated_or_used_before_endpoint(tmp_path):
    generate_synthetic_files(tmp_path)
    source = pd.read_csv(tmp_path / "telemetry.csv")
    long_gap = ["2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23"]
    remove = ((source.resource_id == "STO-R2-01") & (source.tenant_id == "acme") &
              source.timestamp.isin(long_gap))
    source.loc[~remove].to_csv(tmp_path / "telemetry.csv", index=False)
    dataset = load_dataset(tmp_path)
    assert not dataset.telemetry.loc[
        (dataset.telemetry.resource_id == "STO-R2-01") &
        (dataset.telemetry.tenant_id == "acme") &
        dataset.telemetry.timestamp.isin(long_gap)
    ].shape[0]
    as_of = resource_series(dataset, "STO-R2-01", as_of="2026-01-15")
    date_row = as_of.loc[pd.to_datetime(as_of.timestamp) == pd.Timestamp("2026-01-15")]
    assert len(date_row) == 1 and pd.isna(date_row.iloc[0].value)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_api_health_inventory_filters_alerts_and_get_only(client):
    health_response = client.get("/api/health")
    assert health_response.status_code == 200
    assert health_response.json()["read_only"] is True
    assert "not production-secure" in health_response.json()["auth"]
    dataset_response = client.get("/api/dataset")
    assert dataset_response.json()["label"] == "SYNTHETIC"
    summary = client.get("/api/summary")
    assert summary.status_code == 200
    assert summary.json()["total_resources"] == 10
    resources = client.get("/api/resources", params={"resource_type": "storage", "rack": "R1"})
    assert resources.status_code == 200
    assert [row["resource_id"] for row in resources.json()["items"]] == ["STO-R1-01"]
    alert_response = client.get("/api/alerts")
    assert alert_response.status_code == 200
    assert alert_response.json()["items"]
    validation = summary.json()["validation"]["metrics"]
    assert validation["chosen"]["mae"] < validation["naive"]["mae"]
    assert client.get("/api/resources", params={"sort": "unknown"}).status_code == 422
    assert client.get("/api/tenants").json()["items"] == ["acme", "globex", "initech", "umbra"]
    for method in ("post", "put", "delete"):
        assert getattr(client, method)("/api/health").status_code == 405


def test_tenant_scoping_and_not_found_behavior(client):
    for route in ("/api/summary", "/api/resources", "/api/alerts", "/api/events", "/api/audit"):
        response = client.get(route, params={"tenant": "acme"})
        assert response.status_code == 200, route
        text = json.dumps(response.json()).lower()
        assert "globex" not in text and "initech" not in text and "umbra" not in text
    assert client.get("/api/summary", params={"tenant": "unknown"}).status_code == 404
    assert client.get("/api/resources/STO-R4-01/forecast", params={"tenant": "acme"}).status_code == 404
    assert client.get("/api/resources/NO-SUCH-RESOURCE/forecast").status_code == 404
    forecast_text = json.dumps(client.get(
        "/api/resources/STO-R1-01/forecast", params={"tenant": "acme"}
    ).json()).lower()
    assert "globex" not in forecast_text and "initech" not in forecast_text and "umbra" not in forecast_text


def test_forecast_endpoint_audits_each_call(client):
    before = client.get("/api/audit", params={"tenant": "acme", "resource_id": "STO-R1-01"}).json()["items"]
    for _ in range(2):
        response = client.get("/api/resources/STO-R1-01/forecast", params={"tenant": "acme"})
        assert response.status_code == 200
    after = client.get("/api/audit", params={"tenant": "acme", "resource_id": "STO-R1-01"}).json()["items"]
    assert len(after) == len(before) + 2
    assert after[0]["input_fingerprint"]
    assert after[0]["engine_version"]
    assert after[0]["tenant_scope"] == "acme"

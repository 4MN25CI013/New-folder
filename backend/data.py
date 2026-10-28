from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
START = date(2025, 10, 28)
END = date(2026, 10, 27)
TENANTS = ("acme", "globex", "initech", "umbra")
INVENTORY = [
    ("STO-R1-01", "storage", "R1", "TB", 1000, 900),
    ("STO-R2-01", "storage", "R2", "TB", 1000, 900),
    ("STO-R3-01", "storage", "R3", "TB", 1000, 900),
    ("STO-R4-01", "storage", "R4", "TB", 1000, 900),
    ("PWR-R1-01", "power", "R1", "kW", 10, 8.5),
    ("PWR-R2-01", "power", "R2", "kW", 10, 8.5),
    ("TMP-R2-01", "thermal", "R2", "C", 32, 27),
    ("NET-R1-01", "network", "R1", "Gbps", 100, 80),
    ("NET-R3-01", "network", "R3", "Gbps", 100, 80),
    ("VM-R3-01", "vm", "R3", "count", 500, 475),
]

# Each component is (tenant, start day, base, [(day, active slope), ...]).
COMPONENTS = {
    "STO-R1-01": [("acme", 0, 300, [(0, .9)]), ("globex", 240, 60, [(240, 1.4)])],
    "STO-R2-01": [("acme", 0, 300, [(0, 0)]), ("initech", 0, 100, [(0, .02)])],
    "STO-R3-01": [("initech", 0, 200, [(0, 1.0)]), ("umbra", 0, 100, [(0, .3)])],
    "STO-R4-01": [("globex", 0, 600, [(0, 1.5), (205, -2.0), (245, .3)])],
    "PWR-R1-01": [("acme", 0, 3, [(0, .004)]), ("globex", 0, 2.5, [(0, .002)])],
    "PWR-R2-01": [("umbra", 0, 2, [(0, .004)]), ("initech", 0, 1.5, [(0, .003), (260, .02)])],
    "TMP-R2-01": [("acme", 0, 22, [(0, 0)])],
    "NET-R1-01": [("globex", 0, 20, [(0, .05), (280, .2)]), ("acme", 0, 10, [(0, .03)])],
    "NET-R3-01": [("umbra", 0, 30, [(0, 0)])],
    "VM-R3-01": [("acme", 0, 150, [(0, .2)]), ("globex", 0, 120, [(0, .3), (300, .5)])],
}


@dataclass
class Dataset:
    telemetry: pd.DataFrame
    inventory: pd.DataFrame
    events: pd.DataFrame
    tenants: list[str]
    label: str
    counts: dict[str, int]
    last_timestamp: str
    status: str


def _component_value(base: float, start: int, changes: list[tuple[int, float]], day: int) -> float:
    value = base
    for (change_day, slope), next_change in zip(changes, changes[1:] + [(day + 1, 0)]):
        begin = max(start, change_day)
        finish = min(day, next_change[0])
        if finish > begin:
            value += (finish - begin) * slope
    return value


def generate_synthetic_files(directory: Path = DATA_DIR) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    rows = []
    for day_index in range(365):
        timestamp = START + timedelta(days=day_index)
        for resource_id, components in COMPONENTS.items():
            for tenant, start, base, changes in components:
                if day_index < start:
                    continue
                value = _component_value(base, start, changes, day_index)
                if resource_id == "TMP-R2-01":
                    value += 2 * np.sin(2 * np.pi * day_index / 365)
                if resource_id == "PWR-R1-01" and tenant == "acme" and day_index in (330, 331):
                    value += 2
                if resource_id == "NET-R3-01" and day_index in (350, 351):
                    value += 45
                value += rng.normal(0, max(abs(value) * .004, .001))
                rows.append((timestamp.isoformat(), resource_id, tenant, round(float(value), 5)))
    raw = pd.DataFrame(rows, columns=["timestamp", "resource_id", "tenant_id", "value"])
    # Deliberate quality defects used to demonstrate the loader's counters.
    raw = raw.loc[~((raw.resource_id == "STO-R2-01") & (raw.tenant_id == "acme") &
                    raw.timestamp.isin(["2026-01-15", "2026-01-16"]))]
    raw = pd.concat([raw, raw.iloc[[20]], pd.DataFrame([{
        "timestamp": "2026-04-01", "resource_id": "TMP-R2-01", "tenant_id": "globex", "value": -4.0
    }])], ignore_index=True)
    raw.to_csv(directory / "telemetry.csv", index=False)
    pd.DataFrame(INVENTORY, columns=[
        "resource_id", "resource_type", "rack_id", "unit", "capacity_limit", "safety_threshold"
    ]).to_csv(directory / "inventory.csv", index=False)
    events = [
        ("2026-02-26", "STO-R1-01", "globex", "tenant_onboarding", "Globex tenant onboarded"),
        ("2026-03-23", "PWR-R2-01", "initech", "workload_change", "Initech workload expanded"),
        ("2026-07-24", "STO-R4-01", "globex", "policy_change", "Retention policy tightened"),
        ("2026-07-30", "STO-R4-01", "globex", "capacity_incident", "Storage crossed operational threshold"),
        ("2026-08-07", "NET-R1-01", "globex", "workload_change", "Globex replication workload increased"),
        ("2026-09-26", "VM-R3-01", "globex", "tenant_onboarding", "Globex VM expansion"),
        ("2026-10-12", "PWR-R1-01", "acme", "maintenance", "Acme load test"),
    ]
    pd.DataFrame(events, columns=["date", "resource_id", "tenant_id", "event_type", "description"]).to_csv(
        directory / "events.csv", index=False
    )
    (directory / "DATASET_LABEL.txt").write_text(
        "SYNTHETIC — deterministically generated telemetry; seed 7; no facility measurements.\n",
        encoding="utf-8",
    )


def load_dataset(directory: Path = DATA_DIR) -> Dataset:
    required = [directory / name for name in ("telemetry.csv", "inventory.csv", "events.csv")]
    label_file = directory / "DATASET_LABEL.txt"
    if not any(path.exists() for path in required):
        generate_synthetic_files(directory)
    elif not all(path.exists() for path in required):
        missing = [path.name for path in required if not path.exists()]
        raise FileNotFoundError(f"Incomplete supplied dataset in {directory}; missing: {', '.join(missing)}")
    synthetic = label_file.exists() and "SYNTHETIC" in label_file.read_text(encoding="utf-8").upper()
    label = "SYNTHETIC" if synthetic else "supplied"
    raw = pd.read_csv(required[0])
    required_columns = {"timestamp", "resource_id", "tenant_id", "value"}
    if not required_columns.issubset(raw.columns):
        raise ValueError(f"telemetry.csv must contain columns: {', '.join(sorted(required_columns))}")
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce").dt.normalize()
    raw["value"] = pd.to_numeric(raw["value"], errors="coerce")
    raw = raw.dropna(subset=["timestamp", "resource_id", "tenant_id"])
    raw["timestamp"] = raw["timestamp"].dt.date.astype(str)
    raw = raw.sort_values(["timestamp", "resource_id", "tenant_id"], kind="stable")
    before = len(raw)
    raw = raw.drop_duplicates(["timestamp", "resource_id", "tenant_id"], keep="last")
    duplicates = before - len(raw)
    invalid_mask = raw["value"].isna() | (raw["value"] < 0)
    invalid = int(invalid_mask.sum())
    raw = raw.loc[~invalid_mask].copy()
    inventory = pd.read_csv(required[1])
    events = pd.read_csv(required[2])
    events["date"] = pd.to_datetime(events["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    wide_frames = []
    for (resource_id, tenant), group in raw.groupby(["resource_id", "tenant_id"]):
        series = group.set_index("timestamp")["value"].sort_index()
        days = pd.date_range(series.index.min(), series.index.max(), freq="D").strftime("%Y-%m-%d")
        series = series.reindex(days)
        imputed_until = pd.Series(index=series.index, dtype="object")
        index = 0
        while index < len(series):
            if pd.notna(series.iloc[index]):
                index += 1
                continue
            start = index
            while index < len(series) and pd.isna(series.iloc[index]):
                index += 1
            gap_length = index - start
            if start > 0 and index < len(series) and gap_length <= 3:
                left, right = float(series.iloc[start - 1]), float(series.iloc[index])
                for position in range(start, index):
                    fraction = (position - start + 1) / (gap_length + 1)
                    series.iloc[position] = left + fraction * (right - left)
                    imputed_until.iloc[position] = series.index[index]
        wide_frames.append(pd.DataFrame({
            "timestamp": series.index, "resource_id": resource_id, "tenant_id": tenant,
            "value": series.values, "imputed_until": imputed_until.values,
        }).dropna(subset=["value"]))
    telemetry = pd.concat(wide_frames, ignore_index=True) if wide_frames else pd.DataFrame(
        columns=["timestamp", "resource_id", "tenant_id", "value", "imputed_until"]
    )
    counts = {"duplicates_dropped": int(duplicates), "invalid_values_dropped": invalid, "missing_values_interpolated": 0}
    # Count missing cells filled by short interior interpolation, distinct from deliberately absent raw rows.
    raw_keys = set(zip(raw.timestamp, raw.resource_id, raw.tenant_id))
    filled_keys = set(zip(telemetry.timestamp, telemetry.resource_id, telemetry.tenant_id))
    counts["missing_values_interpolated"] = sum(key not in raw_keys for key in filled_keys)
    status = "clean" if not invalid and not duplicates else "cleaned"
    last_timestamp = str(telemetry.timestamp.max()) if not telemetry.empty else "unavailable"
    return Dataset(telemetry, inventory, events, sorted(telemetry.tenant_id.unique().tolist()),
                   label, counts, last_timestamp, status)


def resource_series(
    dataset: Dataset, resource_id: str, tenant: str | None = None, as_of: date | str | None = None
) -> pd.DataFrame:
    selected = dataset.telemetry.loc[dataset.telemetry.resource_id == resource_id]
    if tenant:
        selected = selected.loc[selected.tenant_id == tenant]
    if selected.empty:
        return pd.DataFrame(columns=["timestamp", "value"])
    selected = selected.copy()
    selected["timestamp"] = pd.to_datetime(selected["timestamp"])
    if as_of is not None:
        cutoff = pd.Timestamp(as_of).normalize()
        selected = selected.loc[selected.timestamp <= cutoff]
        if "imputed_until" in selected:
            later_imputation = pd.to_datetime(selected.imputed_until, errors="coerce") > cutoff
            selected.loc[later_imputation, "value"] = np.nan
    unresolved_dates = set()
    if as_of is not None and "imputed_until" in selected:
        unresolved_dates = set(selected.loc[selected.value.isna(), "timestamp"])
    pivot = selected.pivot_table(index="timestamp", columns="tenant_id", values="value", aggfunc="sum").sort_index()
    pivot = pivot.asfreq("D")
    totals = pivot.sum(axis=1, min_count=1)
    if unresolved_dates:
        totals.loc[totals.index.isin(unresolved_dates)] = np.nan
    return pd.DataFrame({"timestamp": pivot.index, **{c: pivot[c] for c in pivot.columns},
                         "value": totals}).reset_index(drop=True)


def ensure_demo_data() -> Dataset:
    return load_dataset()

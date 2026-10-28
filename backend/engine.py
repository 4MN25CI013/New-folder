from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

ENGINE_VERSION = "1.0.0"


def _date(value: date | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _ols(y: np.ndarray) -> tuple[float, float, float, float]:
    x = np.arange(len(y), dtype=float)
    if len(y) < 3:
        return float(y[-1] - y[0]) if len(y) > 1 else 0.0, 0.0, float(y[-1]) if len(y) else 0.0, 0.0
    slope, intercept = np.polyfit(x, y, 1)
    residuals = y - (intercept + slope * x)
    sigma = float(np.sqrt(np.sum(residuals ** 2) / max(1, len(y) - 2)))
    se = float(sigma / np.sqrt(np.sum((x - x.mean()) ** 2))) if len(y) > 2 else 0.0
    return float(slope), se, float(intercept), sigma


def damped_holt(values: np.ndarray, horizon: int, alpha: float = .3, beta: float = .1, phi: float = .98) -> np.ndarray:
    if not len(values):
        return np.array([])
    level = float(values[0])
    trend = float(values[1] - values[0]) if len(values) > 1 else 0.0
    for observation in values[1:]:
        previous_level = level
        level = alpha * float(observation) + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - previous_level) + (1 - beta) * phi * trend
    return np.array([level + sum(phi ** i for i in range(1, h + 1)) * trend for h in range(1, horizon + 1)])


def _spike_filter(values: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
    if not len(values):
        return values.copy(), []
    series = pd.Series(values, dtype=float)
    median = series.rolling(7, min_periods=1).median()
    deviations = series - median
    mad = deviations.abs().rolling(30, min_periods=7).median().fillna(0)
    flagged = (deviations.abs() > np.maximum(4 * 1.4826 * mad, .01 * median.abs())).to_numpy()
    groups: list[list[int]] = []
    for index in np.flatnonzero(flagged):
        if not groups or index > groups[-1][-1] + 1:
            groups.append([int(index)])
        else:
            groups[-1].append(int(index))
    cleaned = values.copy()
    output = []
    for group in groups:
        first, last = group[0], group[-1]
        before = values[max(0, first - 5):first]
        after = values[last + 1:min(len(values), last + 6)]
        pre = float(np.mean(before)) if len(before) else float(values[first])
        post = float(np.mean(after)) if len(after) else None
        magnitude = float(np.mean(values[group]) - pre)
        if len(after) < 3:
            kind = "unconfirmed_spike"
        elif abs(float(post) - pre) < .5 * abs(magnitude):
            kind = "temporary_spike"
        else:
            kind = "level_shift"
        output.append({"index": first, "end_index": last, "type": kind, "magnitude": magnitude})
        if kind != "level_shift":
            cleaned[group] = median.to_numpy()[group]
    return cleaned, output


def _growth_changes(values: np.ndarray) -> list[int]:
    changes: list[int] = []
    for end in range(28, len(values), 3):
        older = values[end - 28:end - 13]
        newer = values[end - 14:end + 1]
        old_slope, old_se, _, _ = _ols(older)
        new_slope, new_se, _, _ = _ols(newer)
        difference = abs(new_slope - old_slope)
        combined = np.sqrt(old_se ** 2 + new_se ** 2)
        if difference > 4 * combined and difference >= .2 * max(abs(new_slope), abs(old_slope), 1e-8):
            if not changes or end - changes[-1] >= 14:
                changes.append(end)
    return changes


def _method_scores(values: np.ndarray, window_size: int) -> dict[str, float | None]:
    if len(values) < 22:
        return {"ols_mae": None, "holt_mae": None}
    train, actual = values[:-14][-window_size:], values[-14:]
    if len(train) < 3:
        return {"ols_mae": None, "holt_mae": None}
    slope, _, intercept, _ = _ols(train)
    ols_pred = intercept + slope * np.arange(len(train), len(train) + len(actual))
    holt_pred = damped_holt(train, len(actual))
    return {
        "ols_mae": float(np.mean(np.abs(actual - ols_pred))),
        "holt_mae": float(np.mean(np.abs(actual - holt_pred))),
    }


def forecast(
    history: pd.DataFrame,
    inventory: dict[str, Any],
    tenant_values: dict[str, pd.Series] | None = None,
    events: list[dict[str, Any]] | None = None,
    as_of: date | str | None = None,
    include_previous: bool = True,
) -> dict[str, Any]:
    as_of_date = _date(as_of) if as_of is not None else None
    frame = history.copy()
    if frame.empty:
        return {"status": "Insufficient Data", "observations": 0, "days_remaining": None,
                "note": "fewer than 30 observations", "changes": [], "forecast_path": [], "attribution": []}
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "value"]).sort_values("timestamp")
    if as_of_date is not None:
        frame = frame.loc[frame.timestamp.dt.date <= as_of_date]
    frame = frame.drop_duplicates("timestamp", keep="last")
    dates = frame.timestamp.dt.date.tolist()
    as_of_date = as_of_date or (dates[-1] if dates else date.today())
    observed = frame.value.to_numpy(dtype=float)
    n = len(observed)
    threshold = float(inventory["safety_threshold"])
    hard_limit = float(inventory["capacity_limit"])
    current = float(observed[-1]) if n else None
    if n < 30:
        return {"status": "Insufficient Data", "observations": n, "current_value": current,
                "days_remaining": None, "note": "fewer than 30 observations", "changes": [],
                "forecast_path": [], "attribution": [], "events": events or []}

    cleaned, spike_groups = _spike_filter(observed)
    growth_indexes = _growth_changes(cleaned)
    changes = []
    for index in growth_indexes:
        changes.append({"date": dates[index].isoformat(), "type": "growth_rate_change"})
    for group in spike_groups:
        if group["type"] == "level_shift":
            changes.append({"date": dates[group["index"]].isoformat(), "type": "level_shift"})
    changes.sort(key=lambda item: item["date"])
    latest_change_index = max(growth_indexes + [g["index"] for g in spike_groups if g["type"] == "level_shift"],
                              default=0)
    days_since_change = n - 1 - latest_change_index
    window_size = int(np.clip(days_since_change, 14, 60)) if latest_change_index else min(60, n)
    fit_values = cleaned[-window_size:]
    scores = _method_scores(cleaned, window_size)
    chosen_model = "damped_holt" if scores["holt_mae"] is not None and scores["holt_mae"] < scores["ols_mae"] else "ols"

    # OLS statistics deliberately govern exhaustion evidence and confidence, even when Holt wins.
    slope, se, intercept, sigma = _ols(fit_values)
    numerical_floor = np.finfo(float).eps * max(abs(current), 1.0) * 10
    significant_growth = slope > numerical_floor and (slope / se > 2.5 if se > 0 else True)
    gap = max(0.0, threshold - current)
    days_remaining: int | None = None
    low_days: int | None = None
    high_days: int | None = None
    if current >= threshold:
        days_remaining, low_days, high_days = 0, 0, 0
    elif significant_growth:
        projected_days = max(0, int(np.ceil(gap / slope)))
        if projected_days <= 365:
            days_remaining = projected_days
            lower_slope, upper_slope = slope - 1.96 * se, slope + 1.96 * se
            low_days = max(0, int(np.floor(gap / upper_slope))) if upper_slope > 0 else None
            high_days = max(0, int(np.ceil(gap / lower_slope))) if lower_slope > 0 else None
    note = (
        None if days_remaining is not None else
        "no statistically supported growth; exhaustion date not estimated"
        if not significant_growth else
        "projected crossing is beyond the 365-day horizon; exhaustion date not estimated"
    )
    if n < 30:
        status = "Insufficient Data"
    elif current >= threshold or (days_remaining is not None and days_remaining <= 14):
        status = "Critical"
    elif days_remaining is not None and days_remaining <= 30:
        status = "High Risk"
    elif days_remaining is not None and days_remaining <= 90:
        status = "Watch"
    else:
        status = "Healthy"
    confidence = "Low"
    relative_width = float("inf")
    if days_remaining == 0:
        confidence, relative_width = "High", 0
    elif low_days is not None and high_days is not None and days_remaining:
        relative_width = (high_days - low_days) / days_remaining
        confidence = "High" if relative_width < .3 else "Medium" if relative_width < 1 else "Low"

    projected = damped_holt(fit_values, 365) if chosen_model == "damped_holt" else (
        intercept + slope * np.arange(len(fit_values), len(fit_values) + 365)
    )
    if chosen_model == "ols":
        # Rebase the fitted line to the last observed value for the displayed path.
        projected = current + slope * np.arange(1, 366)
    forecast_path = []
    for horizon in (30, 60, 90):
        value = float(projected[horizon - 1])
        band = 1.96 * np.sqrt(sigma ** 2 + (se * horizon) ** 2)
        forecast_path.append({"days": horizon, "date": (dates[-1] + timedelta(days=horizon)).isoformat(),
                              "value": value, "low": value - band, "high": value + band})
    history_points = [{"date": d.isoformat(), "value": float(v)} for d, v in zip(dates[-150:], observed[-150:])]
    chart_path = [{"date": (dates[-1] + timedelta(days=h)).isoformat(), "value": float(projected[h - 1]),
                   "low": float(projected[h - 1] - 1.96 * np.sqrt(sigma ** 2 + (se * h) ** 2)),
                   "high": float(projected[h - 1] + 1.96 * np.sqrt(sigma ** 2 + (se * h) ** 2))}
                  for h in range(1, 366)]

    attribution = []
    positive = {}
    for tenant, series in (tenant_values or {}).items():
        s = series.dropna()
        if len(s) < 2:
            continue
        recent_start = s.index[-1] - pd.Timedelta(days=30) if isinstance(s.index, pd.DatetimeIndex) else None
        recent = s.loc[s.index >= recent_start] if recent_start is not None else s.iloc[-30:]
        delta = float(s.iloc[-1] - recent.iloc[0])
        positive[tenant] = max(0.0, delta)
    total_positive = sum(positive.values())
    for tenant, delta in sorted(positive.items(), key=lambda item: item[1], reverse=True):
        if delta:
            attribution.append({"tenant": tenant, "change_30d": delta,
                                "share_of_positive_growth": delta / total_positive if total_positive else 0,
                                "wording": "likely contributor - association only, causation not established"})
    if not attribution:
        attribution = [{"tenant": None, "wording": "cause unknown"}]

    previous_forecast = None
    recent = [item for item in changes if (as_of_date - date.fromisoformat(item["date"])).days <= 60]
    if include_previous and recent:
        last_change = max(date.fromisoformat(item["date"]) for item in recent)
        previous_as_of = last_change - timedelta(days=1)
        old = forecast(history, inventory, tenant_values, events, previous_as_of, include_previous=False)
        previous_forecast = {"as_of": previous_as_of.isoformat(), "status": old["status"],
                             "days_remaining": old.get("days_remaining"),
                             "model": old.get("model"), "crossing_date": old.get("crossing_date")}

    crossing_date = (dates[-1] + timedelta(days=days_remaining)).isoformat() if days_remaining is not None else None
    growth_week = slope * 7
    return {
        "status": status, "observations": n, "as_of": dates[-1].isoformat(), "current_value": current,
        "safety_threshold": threshold, "capacity_limit": hard_limit,
        "utilisation_pct": 100 * current / threshold, "days_remaining": days_remaining,
        "crossing_date": crossing_date, "range": {"low_days": low_days, "high_days": high_days},
        "confidence": confidence, "relative_range_width": relative_width, "note": note,
        "weekly_growth": growth_week, "slope_per_day": slope, "slope_standard_error": se,
        "model": chosen_model, "method_scores": scores, "fit_window_days": window_size,
        "changes": changes, "spikes": [{"date": dates[g["index"]].isoformat(), "end_date": dates[g["end_index"]].isoformat(),
                                        "type": g["type"], "magnitude": g["magnitude"]} for g in spike_groups],
        "history": history_points, "forecast_path": chart_path,
        "forecasts": forecast_path, "attribution": attribution, "events": events or [],
        "previous_forecast": previous_forecast,
    }

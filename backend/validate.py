from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.data import Dataset, resource_series
from backend.engine import damped_holt, forecast


def _errors(actual: list[float], predicted: list[float]) -> dict[str, float | None]:
    if not actual:
        return {"mae": None, "rmse": None}
    error = np.asarray(actual) - np.asarray(predicted)
    return {"mae": float(np.mean(np.abs(error))), "rmse": float(np.sqrt(np.mean(error ** 2)))}


def run_validation(dataset: Dataset) -> dict[str, Any]:
    model_actual: dict[str, list[float]] = {k: [] for k in ("chosen", "ols", "damped_holt", "naive")}
    model_predicted: dict[str, list[float]] = {k: [] for k in model_actual}
    historical_crossings = 0
    warning_count = 0
    false_alarms = 0
    lead_times: list[int] = []
    reasons = []
    metadata = dataset.inventory.set_index("resource_id")

    for resource_id in metadata.index:
        history = resource_series(dataset, resource_id)
        if len(history) < 60:
            reasons.append(f"{resource_id}: fewer than 60 observations")
            continue
        values = history.value.to_numpy(dtype=float)
        timestamps = pd.to_datetime(history.timestamp)
        threshold = float(metadata.loc[resource_id, "safety_threshold"])
        above = values >= threshold
        crossings = np.flatnonzero(above & ~np.r_[False, above[:-1]])
        historical_crossings += len(crossings)
        last_origin = len(values) - 91
        crossing_warnings: dict[int, list[int]] = {}
        if last_origin < 60:
            reasons.append(f"{resource_id}: insufficient origins with an observable 90-day horizon")
            continue
        for origin in range(60, last_origin + 1, 7):
            origin_date = timestamps.iloc[origin].date()
            train = resource_series(dataset, resource_id, as_of=origin_date)
            actual30 = float(values[origin + 30])
            output = forecast(train, metadata.loc[resource_id].to_dict(), as_of=origin_date, include_previous=False)
            pred30 = output.get("forecasts", [])
            if pred30:
                model_actual["chosen"].append(actual30)
                model_predicted["chosen"].append(float(pred30[0]["value"]))
            fit_values = train.value.dropna().to_numpy(dtype=float)[-60:]
            if len(fit_values) < 3:
                continue
            x = np.arange(len(fit_values), dtype=float)
            slope, intercept = np.polyfit(x, fit_values, 1)
            ols_prediction = float(intercept + slope * (len(fit_values) - 1 + 30))
            holt_prediction = float(damped_holt(fit_values, 30)[-1])
            naive_prediction = float(fit_values[-1])
            for name, prediction in (("ols", ols_prediction), ("damped_holt", holt_prediction),
                                     ("naive", naive_prediction)):
                model_actual[name].append(actual30)
                model_predicted[name].append(prediction)

            future_cross = np.flatnonzero(values[origin + 1:origin + 91] >= threshold)
            if output.get("days_remaining") is not None and output["days_remaining"] <= 90:
                warning_count += 1
                if len(future_cross):
                    crossing_index = origin + 1 + int(future_cross[0])
                    crossing_warnings.setdefault(crossing_index, []).append(origin)
                else:
                    false_alarms += 1
        for crossing_index, origins in crossing_warnings.items():
            first_warning_date = timestamps.iloc[min(origins)].date()
            crossing_date = timestamps.iloc[crossing_index].date()
            lead_times.append((crossing_date - first_warning_date).days)

    errors = {name: _errors(model_actual[name], model_predicted[name]) for name in model_actual}
    warnings_issued = warning_count
    metric_limitations = {
        name: "No eligible 30-day rolling-origin predictions were available."
        for name, result in errors.items() if result["mae"] is None
    }
    return {
        "metrics": errors,
        "metric_limitations": metric_limitations,
        "warning_evaluation": {
            "historical_crossings": historical_crossings,
            "warnings_issued": warnings_issued,
            "false_alarms": false_alarms,
            "false_alarm_rate": false_alarms / warnings_issued if warnings_issued else None,
            "median_warning_lead_time_days": float(np.median(lead_times)) if lead_times else None,
            "correct_warning_leads": len(lead_times),
            "warning_unit": "Each eligible seven-day rolling-origin warning is counted; repeated weekly warnings are not deduplicated.",
            "limitations": (
                ["No eligible warning-origin evaluations were available."]
                if warnings_issued == 0 else
                ["No correct warnings with an observable crossing were available."]
                if not lead_times else []
            ),
        },
        "origins": min((len(v) for v in model_actual.values()), default=0),
        "note": "Backtest results are from synthetic data with few events; they are measured only on these rolling-origin periods and do not establish outage reduction.",
        "limitations": reasons,
    }

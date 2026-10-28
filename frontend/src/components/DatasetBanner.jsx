export default function DatasetBanner({ dataset }) {
  return <div className="banner">
    <div className="dataset-copy">
      <span className={`dataset-tag ${dataset?.label === "SYNTHETIC" ? "synthetic" : ""}`}>{dataset?.label || "Loading dataset"}</span>
      <span>Last data: {dataset?.last_timestamp || "—"}</span>
      <span>Quality: {dataset?.validation_status || "checking"}</span>
      {dataset?.rows_dropped && <span>Cleaned: {dataset.rows_dropped.duplicates_dropped} duplicate, {dataset.rows_dropped.invalid_values_dropped} invalid, {dataset.rows_dropped.missing_values_interpolated} gap fills</span>}
    </div>
    <strong>Advisory only — read-only — no authentication, not production-secure</strong>
  </div>;
}

export default function Cards({ summary }) {
  const validation = summary?.validation?.metrics || {};
  const warning = summary?.validation?.warning_evaluation || {};
  const fmt = (value) => value == null ? "n/a" : Number(value).toFixed(2);
  const cards = [
    ["Resources monitored", summary?.total_resources ?? "—", "Live inventory"],
    ["At risk", summary?.at_risk ?? "—", "Watch or higher"],
    ["Within 7 days", summary?.within_7_days ?? "—", "Forecast crossings"],
    ["Within 30 days", summary?.within_30_days ?? "—", "Forecast crossings"],
    ["Within 90 days", summary?.within_90_days ?? "—", "Forecast crossings"],
    ["Backtest MAE / RMSE", `${fmt(validation.chosen?.mae)} / ${fmt(validation.chosen?.rmse)}`, "Chosen model"],
    ["False-alarm rate", warning.false_alarm_rate == null ? "n/a" : `${(warning.false_alarm_rate * 100).toFixed(1)}%`, `${warning.false_alarms ?? 0} false alarms`],
    ["Median warning lead", warning.median_warning_lead_time_days == null ? "n/a" : `${Math.round(warning.median_warning_lead_time_days)} days`, "Measured backtest"]
  ];
  return <section className="card-grid" aria-label="Capacity overview">
    {cards.map(([label, value, note]) => <article className="metric-card" key={label}>
      <span className="metric-label">{label}</span><strong>{value}</strong><span className="metric-note">{note}</span>
    </article>)}
  </section>;
}

const badgeType = {
  sustained_growth: "Sustained growth",
  regime_change: "Regime change",
  possible_spike_unconfirmed: "Possible spike — unconfirmed"
};
export default function AlertCentre({ alerts, onSelect }) {
  return <section className="panel">
    <div className="section-heading"><div><p className="eyebrow">Operational priorities</p><h2>Alert centre</h2></div><span className="subtle">{alerts.length} active signals</span></div>
    {alerts.length === 0 ? <p className="empty">No active capacity warnings for this view.</p> :
      <div className="alert-list">{alerts.map(alert => <article className="alert-card" key={alert.resource} onClick={() => onSelect(alert.resource)} tabIndex="0" onKeyDown={e => e.key === "Enter" && onSelect(alert.resource)}>
        <div className="alert-top">
          <div><span className={`risk-badge ${riskClass(alert.risk_level)}`}>{alert.risk_level}</span><span className="type-badge">{badgeType[alert.alert_type] || alert.alert_type}</span></div>
          <button className="text-button" onClick={e => { e.stopPropagation(); onSelect(alert.resource); }}>Details <span aria-hidden="true">→</span></button>
        </div>
        <h3>{alert.resource} <small>{alert.rack}</small></h3>
        <p className="alert-summary">{alert.predicted_crossing_date === "not estimable" ? "No statistically supported exhaustion date" : `Safety threshold forecast: ${alert.predicted_crossing_date} · ${alert.lead_time_days} days`}</p>
        <ul className="evidence">{alert.evidence?.slice(0, 3).map((item, index) => <li key={index}>{item}</li>)}</ul>
        <div className="alert-footer"><span>{alert.likely_contributor?.tenant ? `${alert.likely_contributor.tenant} — likely contributor` : alert.likely_contributor?.wording}</span><span>{alert.confidence || "n/a"} confidence</span></div>
        <p className="recommendation"><b>Advisory:</b> {alert.recommended_action}</p>
      </article>)}</div>}
  </section>;
}
export function riskClass(status = "") {
  return status.toLowerCase().replaceAll(" ", "-");
}

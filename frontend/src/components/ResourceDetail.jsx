import { useEffect, useState } from "react";
import {
  Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis, BarChart, Bar, Cell
} from "recharts";
import { riskClass } from "./AlertCentre.jsx";

function DetailChart({ detail }) {
  const series = new Map();
  detail.history?.forEach(p => series.set(p.date, { date: p.date, observed: p.value }));
  detail.forecast_path?.forEach(p => {
    series.set(p.date, { ...(series.get(p.date) || {}), date: p.date, prediction: p.value, low: p.low, high: p.high });
  });
  const points = [...series.values()].sort((a, b) => a.date.localeCompare(b.date));
  const changes = new Set((detail.changes || []).map(x => x.date));
  return <div className="chart-box">
    <ResponsiveContainer width="100%" height={310}>
      <ComposedChart data={points} margin={{ top: 12, right: 12, left: 0, bottom: 4 }}>
        <CartesianGrid stroke="#243044" strokeDasharray="3 3" />
        <XAxis dataKey="date" tick={{ fill: "#8290a5", fontSize: 10 }} tickFormatter={v => v.slice(5)} minTickGap={30} />
        <YAxis tick={{ fill: "#8290a5", fontSize: 10 }} width={48} />
        <Tooltip contentStyle={{ background: "#111b2b", border: "1px solid #29384d", borderRadius: 8 }} labelStyle={{ color: "#eaf0f8" }} />
        <ReferenceLine y={detail.safety_threshold} stroke="#f59e0b" strokeDasharray="5 4" label={{ value: "Safety", fill: "#fbbf24", position: "insideTopRight", fontSize: 10 }} />
        <ReferenceLine y={detail.capacity_limit} stroke="#ef4444" strokeDasharray="5 4" label={{ value: "Hard limit", fill: "#f87171", position: "insideTopRight", fontSize: 10 }} />
        <ReferenceLine x={detail.as_of} stroke="#94a3b8" strokeDasharray="2 4" label={{ value: "Today", fill: "#cbd5e1", position: "insideTopLeft", fontSize: 10 }} />
        {[...changes].map(date => <ReferenceLine key={date} x={date} stroke="#a78bfa" strokeDasharray="3 3" />)}
        <Area dataKey="high" stroke="none" fill="#3b82f6" fillOpacity={0.12} connectNulls />
        <Area dataKey="low" stroke="none" fill="#0f1726" fillOpacity={1} connectNulls />
        <Line dataKey="observed" name="Observed" stroke="#38bdf8" strokeWidth={2} dot={false} connectNulls />
        <Line dataKey="prediction" name="Forecast" stroke="#a78bfa" strokeWidth={2} strokeDasharray="6 4" dot={false} connectNulls />
      </ComposedChart>
    </ResponsiveContainer>
    <div className="chart-legend"><span><i className="legend-observed" />Observed</span><span><i className="legend-forecast" />Forecast / 95% band</span><span><i className="legend-change" />Detected change</span></div>
  </div>;
}

export default function ResourceDetail({ resourceId, tenant, onClose }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [notificationRows, setNotificationRows] = useState([]);
  useEffect(() => {
    let active = true;
    const query = tenant ? `?tenant=${encodeURIComponent(tenant)}` : "";
    setDetail(null); setError("");
    fetch(`/api/resources/${encodeURIComponent(resourceId)}/forecast${query}`)
      .then(async r => { if (!r.ok) throw new Error((await r.json()).detail || "Unable to load forecast"); return r.json(); })
      .then(value => active && setDetail(value))
      .catch(e => active && setError(e.message));
    const notificationQuery = new URLSearchParams({ limit: "20", resource_id: resourceId });
    if (tenant) notificationQuery.set("tenant", tenant);
    fetch(`/api/notifications?${notificationQuery}`)
      .then(async r => { if (!r.ok) throw new Error("Unable to load notification history"); return r.json(); })
      .then(value => active && setNotificationRows(value.items || []))
      .catch(() => active && setNotificationRows([]));
    return () => { active = false; };
  }, [resourceId, tenant]);
  if (error) return <section className="panel detail-panel"><button className="close-button" onClick={onClose}>Close</button><p className="error">{error}</p></section>;
  if (!detail) return <section className="panel detail-panel"><p className="loading">Loading forecast…</p></section>;
  return <section className="panel detail-panel">
    <div className="section-heading"><div><p className="eyebrow">Resource forecast</p><h2>{resourceId}</h2></div><button className="close-button" onClick={onClose}>Close</button></div>
    <div className="detail-meta"><span className={`risk-badge ${riskClass(detail.status)}`}>{detail.status}</span><span>{detail.resource.resource_type} · Rack {detail.resource.rack_id}</span><span>{detail.current_value?.toFixed(2)} / {detail.safety_threshold} {detail.resource.unit} safety threshold</span><span>{detail.days_remaining == null ? "Exhaustion date not estimated" : `${detail.days_remaining} days · ${detail.confidence} confidence`}</span></div>
    <DetailChart detail={detail} />
    {detail.previous_forecast && <div className="change-banner"><b>Forecast updated after a recent regime change.</b> Previous ({detail.previous_forecast.as_of}): {detail.previous_forecast.days_remaining ?? "not estimable"} days → current: {detail.days_remaining ?? "not estimable"} days.</div>}
    <div className="detail-grid">
      <div className="subpanel"><h3>Forecast &amp; method</h3><p>Selected: <b>{detail.model}</b> · Fit window: {detail.fit_window_days} days</p><p>Holdout MAE — OLS: {fmt(detail.method_scores?.ols_mae)} · Holt: {fmt(detail.method_scores?.holt_mae)}</p>
        <div className="mini-forecasts">{detail.forecasts?.map(f => <div key={f.days}><b>{f.days}d</b><span>{f.value.toFixed(2)} {detail.resource.unit}</span><small>95%: {f.low.toFixed(2)} – {f.high.toFixed(2)}</small></div>)}</div>
        {detail.note && <p className="subtle">{detail.note}</p>}<p className="subtle">Seasonality is not modelled; thermal estimates use the same statistical evidence gate.</p>
      </div>
      <div className="subpanel"><h3>Tenant trend · last 30 days</h3>
        {detail.attribution?.[0]?.tenant ? <ResponsiveContainer width="100%" height={180}><BarChart data={detail.attribution} layout="vertical" margin={{ left: 10, right: 20 }}>
          <XAxis type="number" hide /><YAxis type="category" dataKey="tenant" tick={{ fill: "#9eacc0", fontSize: 11 }} width={70} />
          <Tooltip contentStyle={{ background: "#111b2b", border: "1px solid #29384d" }} />
          <Bar dataKey="change_30d" name="Change" radius={[0, 4, 4, 0]}>{detail.attribution.map((_, i) => <Cell key={i} fill={i === 0 ? "#38bdf8" : "#54708d"} />)}</Bar>
        </BarChart></ResponsiveContainer> : <p>Cause unknown — no tenant showed positive growth.</p>}
        <p className="subtle">Likely contributor — association only, causation not established.</p>
      </div>
      <div className="subpanel"><h3>Detected changes</h3>{detail.changes?.length ? <ul className="plain-list">{detail.changes.slice(-8).map((change, i) => <li key={i}><b>{change.date}</b> · {change.type}</li>)}</ul> : <p>No recent growth change detected.</p>}</div>
      <div className="subpanel"><h3>Related events</h3>{detail.events?.length ? <ul className="plain-list">{detail.events.map((event, i) => <li key={i}><b>{event.date}</b> · {event.description}</li>)}</ul> : <p>No logged events in this view.</p>}</div>
      <div className="subpanel audit-panel"><h3>Forecast audit history</h3><div className="table-scroll"><table><thead><tr><th>Run at</th><th>Model / version</th><th>Data range</th><th>Fingerprint</th><th>Status</th></tr></thead><tbody>
        {detail.audit_history?.map(run => <tr key={run.id}><td>{run.run_at}</td><td>{run.model} / {run.engine_version}</td><td>{run.data_start} – {run.data_end}</td><td><code>{run.input_fingerprint}</code></td><td>{run.status}</td></tr>)}
      </tbody></table></div></div>
      <div className="subpanel audit-panel"><h3>Notification history</h3><div className="table-scroll"><table><thead><tr><th>Time</th><th>Level</th><th>Channel</th><th>Status</th><th>Event</th></tr></thead><tbody>
        {notificationRows.map(row => <tr key={row.id}><td>{row.sent_at}</td><td>{row.risk_level}</td><td>{row.channel}</td><td>{row.status.replaceAll("_", " ")}</td><td>{row.event_type}</td></tr>)}
      </tbody></table></div>{!notificationRows.length && <p className="subtle">No notification records for this resource in the current view.</p>}</div>
    </div>
  </section>;
}
function fmt(value) { return value == null ? "n/a" : Number(value).toFixed(3); }

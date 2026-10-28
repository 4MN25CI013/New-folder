import { useEffect, useState } from "react";

export default function Notifications({ tenant }) {
  const [status, setStatus] = useState(null);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const query = new URLSearchParams({ limit: "100" });
    if (tenant) query.set("tenant", tenant);
    Promise.all([
      fetch(`/api/notifications/status${tenant ? `?tenant=${encodeURIComponent(tenant)}` : ""}`).then(readApi),
      fetch(`/api/notifications?${query}`).then(readApi)
    ]).then(([settings, history]) => {
      if (!active) return;
      setStatus(settings);
      setItems(history.items || []);
    }).catch(reason => {
      if (active) setError(reason.message || "Unable to load notification settings.");
    }).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [tenant]);

  return <section className="panel notifications-page">
    <div className="section-heading">
      <div><p className="eyebrow">Stay informed away from the dashboard</p><h2>Mobile notifications</h2></div>
      <span className={`notify-state ${status?.enabled ? "enabled" : "disabled"}`}>{status?.enabled ? "Enabled" : "Disabled"}</span>
    </div>
    {loading && <p className="loading">Loading notification configuration and history…</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {status && <>
      <p className="notify-disclaimer">Advisory only — notifications report forecasts and never trigger facility actions. There is no authentication.</p>
      <div className="notify-summary">
        <div><span>Configured channels</span><strong>{status.channels?.length ? status.channels.join(", ") : "none"}</strong></div>
        <div><span>Minimum alert level</span><strong>{status.min_level}</strong></div>
        <div><span>Repeat cooldown</span><strong>{status.cooldown_hours} hours</strong></div>
        <div><span>Check interval</span><strong>{status.interval_minutes} minutes</strong></div>
        <div><span>Last cycle</span><strong>{status.last_cycle_at ? new Date(status.last_cycle_at).toLocaleString() : "not run yet"}</strong></div>
        <div><span>Delivery mode</span><strong>{status.dry_run ? "Dry run" : "Live / configured channels"}</strong></div>
      </div>
      {!tenant && status.ntfy_topic && <div className="topic-box"><span>Operator ntfy topic</span><code>{status.ntfy_topic}</code><small>Anyone who knows a public ntfy.sh topic can subscribe to it. Keep this value private.</small></div>}
      <div className="notify-setup">
        <h3>Set up phone delivery</h3>
        <ol>
          <li>Install the free <b>ntfy</b> app from Google Play on Android or the App Store on iOS.</li>
          <li>Configure a long random topic in <code>.env</code> as <code>NTFY_TOPIC</code>, then restart the backend. The topic is displayed here only in Operator view.</li>
          <li>Subscribe to the exact topic in the ntfy app. No account is required for public ntfy.sh topics.</li>
          <li>Run <code>python -m backend.notify --test</code> and check for the test notification on your phone.</li>
          <li>Optionally configure Telegram or SMTP email channels in <code>.env</code>.</li>
        </ol>
        <p>Phone delivery needs internet access. With ntfy.sh, messages pass through a third-party server and a public topic is only as private as its name. Use a long unguessable topic, a self-hosted server/token, and keep tenant-sensitive details minimal. This prototype still has no authentication.</p>
      </div>
    </>}
    <div className="notification-history">
      <div className="section-heading"><div><p className="eyebrow">Delivery audit</p><h3>Notification history</h3></div><span className="subtle">{items.length} records</span></div>
      <div className="table-scroll"><table>
        <thead><tr><th>Time</th><th>Resource</th><th>Level</th><th>Channel</th><th>Status</th><th>Event</th></tr></thead>
        <tbody>{items.map(row => <tr key={row.id}>
          <td>{new Date(row.sent_at).toLocaleString()}</td><td>{row.resource_id}</td>
          <td><span className={`risk-badge ${riskClass(row.risk_level)}`}>{row.risk_level}</span></td>
          <td>{row.channel}</td><td><span className={`notify-log-status ${row.status}`}>{row.status.replaceAll("_", " ")}</span></td>
          <td>{row.event_type}</td>
        </tr>)}</tbody>
      </table></div>
      {!loading && items.length === 0 && <p className="empty">No notification records in this view yet.</p>}
    </div>
  </section>;
}

async function readApi(response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function riskClass(status = "") {
  return status.toLowerCase().replaceAll(" ", "-");
}

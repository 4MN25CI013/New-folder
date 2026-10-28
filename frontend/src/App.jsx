import { useCallback, useEffect, useMemo, useState } from "react";
import Cards from "./components/Cards.jsx";
import DatasetBanner from "./components/DatasetBanner.jsx";
import AlertCentre from "./components/AlertCentre.jsx";
import Inventory from "./components/Inventory.jsx";
import ResourceDetail from "./components/ResourceDetail.jsx";
import Notifications from "./components/Notifications.jsx";
import "./notifications.css";

export default function App() {
  const [tenant, setTenant] = useState(() => new URLSearchParams(window.location.search).get("tenant") || "");
  const [tenants, setTenants] = useState([]);
  const [dataset, setDataset] = useState(null);
  const [summary, setSummary] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(() => new URLSearchParams(window.location.search).get("resource") || "");
  const [activePage, setActivePage] = useState("dashboard");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState({ q: "", resource_type: "", rack: "", risk: "", sort: "risk" });
  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (tenant) params.set("tenant", tenant);
    Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
    return params.toString() ? `?${params}` : "";
  }, [tenant, filters]);

  useEffect(() => {
    fetch("/api/tenants").then(readApi).then(data => setTenants(data.items || []))
      .catch(e => setError(`Unable to load tenant list: ${e.message}`));
    fetch("/api/dataset").then(async r => { if (!r.ok) throw new Error("Dataset status unavailable"); return r.json(); })
      .then(setDataset).catch(e => setError(e.message));
  }, []);

  const refresh = useCallback(() => {
    let active = true;
    setLoading(true); setError("");
    Promise.all([
      fetch(`/api/summary${tenant ? `?tenant=${encodeURIComponent(tenant)}` : ""}`).then(readApi),
      fetch(`/api/alerts${tenant ? `?tenant=${encodeURIComponent(tenant)}` : ""}`).then(readApi),
      fetch(`/api/resources${query}`).then(readApi)
    ]).then(([sum, alertData, resourceData]) => {
      if (!active) return;
      setSummary(sum); setAlerts(alertData.items || []); setItems(resourceData.items || []);
    }).catch(e => active && setError(e.message)).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [tenant, query]);
  useEffect(() => refresh(), [refresh]);

  return <main className="app-shell">
    <header className="topbar">
      <a className="brand" href="/" aria-label="Capacity Intelligence home"><span className="brand-icon">CI</span><span>Capacity <b>Intelligence</b></span></a>
      <div className="topbar-actions">
        <nav className="page-nav" aria-label="Dashboard sections">
          <button className={activePage === "dashboard" ? "active" : ""} onClick={() => setActivePage("dashboard")}>Overview</button>
          <button className={activePage === "notifications" ? "active" : ""} onClick={() => setActivePage("notifications")}>Notifications</button>
        </nav>
        <div className="viewer"><label htmlFor="tenant-select">Viewing as</label><select id="tenant-select" value={tenant} onChange={e => { setSelected(""); setTenant(e.target.value); }}>
          <option value="">Operator · all tenants</option>{tenants.map(name => <option value={name} key={name}>{name}</option>)}
        </select></div>
      </div>
    </header>
    <section className="hero">
      <div><p className="eyebrow"><span className="live-dot" />Capacity operations · daily outlook</p><h1>See trouble before capacity runs out.</h1><p className="hero-copy">A measured view of capacity trajectories, regime changes and the tenants associated with growth.</p></div>
      <div className="hero-side"><span className="hero-side-label">Read-only workspace</span><strong>Forecast with evidence.<br />Act with lead time.</strong><span>6–10 week hardware planning horizon</span></div>
    </section>
    {error && <div className="error-banner" role="alert"><b>Unable to load dashboard:</b> {error} <button onClick={refresh}>Retry</button></div>}
    <DatasetBanner dataset={dataset} />
    {activePage === "notifications" ? <Notifications tenant={tenant} /> : <>
      {loading && !summary ? <div className="loading large">Loading capacity outlook…</div> : <Cards summary={summary} />}
      {loading && <div className="loading-inline">Refreshing tenant-scoped data…</div>}
      <AlertCentre alerts={alerts} onSelect={setSelected} />
      <Inventory items={items} selected={selected} onSelect={setSelected} filters={filters} setFilters={setFilters} />
      {selected && <ResourceDetail resourceId={selected} tenant={tenant} onClose={() => setSelected("")} />}
    </>}
    <footer><span>Capacity Intelligence <i>AO-02 · Smart Thinkers</i></span><span>Advisory only · no controls are connected</span></footer>
  </main>;
}

async function readApi(response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

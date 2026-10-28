import { riskClass } from "./AlertCentre.jsx";
export default function Inventory({ items, selected, onSelect, filters, setFilters }) {
  return <section className="panel inventory-panel">
    <div className="section-heading"><div><p className="eyebrow">Capacity footprint</p><h2>Resource inventory</h2></div><span className="subtle">{items.length} resources</span></div>
    <div className="filters">
      <input aria-label="Search resources" placeholder="Search resource…" value={filters.q} onChange={e => setFilters({ ...filters, q: e.target.value })} />
      <select aria-label="Filter by type" value={filters.resource_type} onChange={e => setFilters({ ...filters, resource_type: e.target.value })}>
        <option value="">All types</option>{["storage", "power", "thermal", "network", "vm"].map(x => <option key={x}>{x}</option>)}
      </select>
      <select aria-label="Filter by rack" value={filters.rack} onChange={e => setFilters({ ...filters, rack: e.target.value })}>
        <option value="">All racks</option>{["R1", "R2", "R3", "R4"].map(x => <option key={x}>{x}</option>)}
      </select>
      <select aria-label="Filter by risk" value={filters.risk} onChange={e => setFilters({ ...filters, risk: e.target.value })}>
        <option value="">All risk levels</option>{["Critical", "High Risk", "Watch", "Healthy", "Insufficient Data"].map(x => <option key={x}>{x}</option>)}
      </select>
      <select aria-label="Sort resources" value={filters.sort} onChange={e => setFilters({ ...filters, sort: e.target.value })}>
        <option value="risk">Sort: risk</option><option value="days">Sort: days left</option><option value="utilisation">Sort: utilisation</option><option value="growth">Sort: growth</option>
      </select>
    </div>
    <div className="table-scroll"><table>
      <thead><tr><th>Resource</th><th>Type</th><th>Rack</th><th>Utilisation</th><th>Growth / week</th><th>Days left</th><th>Risk</th></tr></thead>
      <tbody>{items.map(item => <tr key={item.resource_id} className={selected === item.resource_id ? "selected" : ""} onClick={() => onSelect(item.resource_id)}>
        <td><b>{item.resource_id}</b></td><td>{item.resource_type}</td><td>{item.rack_id}</td>
        <td>{item.utilisation_pct == null ? "n/a" : `${item.utilisation_pct.toFixed(1)}%`}</td>
        <td>{item.weekly_growth == null ? "n/a" : `${item.weekly_growth.toFixed(2)} ${item.unit}`}</td>
        <td>{item.days_remaining == null ? "n/a" : item.days_remaining}</td>
        <td><span className={`risk-badge ${riskClass(item.status)}`}>{item.status}</span></td>
      </tr>)}</tbody>
    </table>{items.length === 0 && <p className="empty">No resources match these filters.</p>}</div>
  </section>;
}

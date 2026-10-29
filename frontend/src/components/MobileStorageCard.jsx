import { useEffect, useState } from "react";

export default function MobileStorageCard() {
  const [mobileData, setMobileData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [message, setMessage] = useState("");

  const fetchMobileStatus = () => {
    setLoading(true);
    fetch("/api/mobile/status")
      .then((res) => res.json())
      .then((data) => {
        setMobileData(data);
        setLoading(false);
      })
      .catch((err) => {
        setLoading(false);
        setMessage("Unable to reach mobile API: " + err.message);
      });
  };

  useEffect(() => {
    fetchMobileStatus();
    // Poll mobile storage every 15 seconds automatically
    const timer = setInterval(fetchMobileStatus, 15000);
    return () => clearInterval(timer);
  }, []);

  const handleTriggerAlert = () => {
    setTriggering(true);
    setMessage("");
    fetch("/api/mobile/trigger-alert", { method: "POST" })
      .then((res) => res.json())
      .then((res) => {
        setTriggering(false);
        if (res.triggered_phone) {
          setMessage("✅ Modal pop-up dialog triggered on your phone screen!");
        } else {
          setMessage("⚠️ Alert triggered on PC. Connect phone via USB to see on phone.");
        }
      })
      .catch((err) => {
        setTriggering(false);
        setMessage("Error triggering alert: " + err.message);
      });
  };

  const isConnected = mobileData?.connected && mobileData?.capacity_gb > 0;
  const isCritical = mobileData?.is_critical;
  const percent = mobileData?.percent_used || 0;

  return (
    <section className="panel mobile-storage-panel" aria-label="Connected Mobile Device">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Real-Time Hardware Telemetry</p>
          <h2>📱 Connected Mobile Storage Monitor</h2>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <span className={`risk-badge ${isCritical ? "critical" : (isConnected ? "healthy" : "insufficient-data")}`}>
            {isCritical ? "CRITICAL (≥ 85%)" : (isConnected ? "HEALTHY (< 85%)" : "DISCONNECTED")}
          </span>
          <button className="text-button" onClick={fetchMobileStatus} title="Refresh Storage">
            🔄 Refresh
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px", marginTop: "12px" }}>
        {/* Device Info Card */}
        <div className="subpanel" style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <div>
            <span style={{ fontSize: "11px", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Device Information
            </span>
            <h3 style={{ margin: "4px 0 0", fontSize: "17px", color: "#f1f6fc" }}>
              {mobileData?.device_name || "No phone connected"}
            </h3>
            <p style={{ margin: "2px 0 0", fontSize: "11px", color: "var(--cyan)" }}>
              {mobileData?.connection_mode || "Plug phone in via USB"}
            </p>
          </div>

          <div style={{ fontSize: "12px", color: "#aab8cb", lineHeight: "1.6" }}>
            <div>Partition: <b>{mobileData?.storage_name || "—"}</b></div>
            <div>Alert Threshold: <b style={{ color: "#f87171" }}>85%</b></div>
          </div>
          {mobileData?.notice && (
            <div style={{ padding: "8px 10px", background: "rgba(245, 158, 11, 0.15)", border: "1px solid #f59e0b", borderRadius: "6px", fontSize: "11px", color: "#fbbf24", lineHeight: "1.4" }}>
              ⚠️ {mobileData.notice}
            </div>
          )}
        </div>

        {/* Capacity Metrics & Gauge */}
        <div className="subpanel" style={{ display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ fontSize: "12px", color: "var(--muted)" }}>Utilization ({percent}%)</span>
              <strong style={{ fontSize: "15px", color: isCritical ? "#f87171" : "#34d399" }}>
                {mobileData?.used_gb || 0} GB / {mobileData?.capacity_gb || 0} GB
              </strong>
            </div>

            {/* Progress Bar */}
            <div style={{ width: "100%", height: "14px", background: "#152337", borderRadius: "8px", overflow: "hidden", border: "1px solid #24364d" }}>
              <div
                style={{
                  width: `${Math.min(percent, 100)}%`,
                  height: "100%",
                  background: isCritical
                    ? "linear-gradient(90deg, #f97316, #ef4444)"
                    : "linear-gradient(90deg, #3b82f6, #10b981)",
                  borderRadius: "7px",
                  transition: "width 0.5s ease-in-out",
                }}
              />
            </div>

            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: "var(--muted)", marginTop: "6px" }}>
              <span>0 GB</span>
              <span>Remaining Free: <b style={{ color: "#e2e8f0" }}>{mobileData?.free_gb || 0} GB</b></span>
              <span>{mobileData?.capacity_gb || 0} GB</span>
            </div>
          </div>

          {/* Action Trigger Button */}
          <div style={{ marginTop: "14px", display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
            <button
              onClick={handleTriggerAlert}
              disabled={triggering}
              style={{
                background: isCritical ? "linear-gradient(135deg, #b91c1c, #dc2626)" : "linear-gradient(135deg, #1d4ed8, #2563eb)",
                color: "#fff",
                border: "none",
                borderRadius: "8px",
                padding: "8px 16px",
                fontSize: "12px",
                fontWeight: "600",
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                boxShadow: isCritical ? "0 4px 15px rgba(239, 68, 68, 0.4)" : "0 4px 12px rgba(37, 99, 235, 0.3)",
              }}
            >
              {triggering ? "Triggering..." : "🚨 Pop-up Alert on Phone Screen"}
            </button>
            {message && <span style={{ fontSize: "11px", color: "#6ee7b7" }}>{message}</span>}
          </div>
        </div>
      </div>
    </section>
  );
}


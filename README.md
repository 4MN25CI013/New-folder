# Capacity Intelligence

**Seeing trouble before capacity runs out** — an advisory-only, read-only capacity forecasting prototype for the AO-02 AI-Ops hackathon (Smart Thinkers, UG-13.9).

Capacity Intelligence estimates time-to-safety-threshold for storage, power, thermal, network and VM resources. It highlights data quality, regime changes, possible transient spikes, the tenants associated with growth, forecast uncertainty and logged events. The app does not connect to facility controls and does not automate procurement or remediation.

> **Prototype security notice:** There is **no authentication**. The `tenant` query parameter only simulates a tenant session; it is not an access-control boundary and this prototype is **not production-secure**. Do not expose it to untrusted users or real tenant data. The API only exposes read routes, and no facility-control interfaces exist.

## Architecture

```text
 React + Vite + Recharts (localhost:5173)
      | GET /api/* (dev proxy)
      v
 FastAPI + deterministic pandas/numpy engine (localhost:8000)
      |                      |
      v                      v
 CSV telemetry         SQLite forecast audit
 CSV inventory         (data/audit.db)
 CSV events
      |
 cleaning, spike filter, regime detection, competing forecasts,
 threshold/uncertainty estimate, attribution, rolling-origin backtest
```

The generated demo dataset is marked **SYNTHETIC** in the API, interface and `data/DATASET_LABEL.txt`. It is not a measurement of a real facility. Synthetic telemetry is deterministic (seed 7), contains 365 daily dates through 2026-10-27, and includes planted data-quality defects. A complete supplied set of `telemetry.csv`, `inventory.csv`, and `events.csv` in `data/` is loaded as supplied unless its label file says `SYNTHETIC`.

## Requirements

- Python 3.10 or later (the current environment is also compatible with newer Python releases)
- Node.js and npm
- Windows PowerShell, or macOS/Linux shell
- No paid APIs or external LLMs. Forecasting is local. Optional phone delivery uses configured ntfy, Telegram or SMTP services; those channels require internet access and their messages leave the machine.

## Setup and run

### Windows PowerShell

From the project root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (!$npmCommand) {
    $packageRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $nodePackage = Get-ChildItem $packageRoot -Directory -Filter "OpenJS.NodeJS.LTS*" -ErrorAction SilentlyContinue | Select-Object -First 1
    $nodeDirectory = if ($nodePackage) {
        Get-ChildItem $nodePackage.FullName -Directory -Filter "node-v*" | Select-Object -First 1
    }
    if (!$nodeDirectory) { throw "Install Node.js LTS and ensure npm is available." }
    $env:PATH = "$($nodeDirectory.FullName);$env:PATH"
}
npm.cmd install
cd ..
```

Start the backend and frontend in separate PowerShell windows:

```powershell
# Window 1, project root
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

```powershell
# Window 2, project root
cd frontend
npm run dev
```

Open `http://localhost:5173`. Check the backend at `http://localhost:8000/api/health`.

### macOS / Linux

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend
npm install
cd ..
```

Start the backend and frontend in separate terminals:

```bash
# Terminal 1, project root
source .venv/bin/activate
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

```bash
# Terminal 2, project root
cd frontend
npm run dev
```

Open `http://localhost:5173`. Check the backend at `http://localhost:8000/api/health`.

### Optional launch helpers

`scripts/run_dev.ps1` starts both development servers in separate windows on Windows and resolves a user-scoped WinGet Node.js install when `npm` is not on `PATH`. `scripts/run_dev.sh` starts the two processes on macOS/Linux; stop them with Ctrl+C. On Windows, use this helper if the Node.js installer did not add `npm` to your `PATH`.

### Production-style single-process demo

```powershell
cd frontend
npm install
npm run build
cd ..
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

When `frontend/dist` exists, FastAPI serves the built UI at `http://localhost:8000/`. CORS is restricted to `http://localhost:5173` and `http://localhost:8000`.

## API

All application endpoints are GET-only. POST/PUT/DELETE requests to these paths return 405.

| Route | Purpose |
|---|---|
| `/api/health` | Health, read-only declaration and prototype authentication notice |
| `/api/dataset` | Dataset label, latest timestamp, cleaner counts and status |
| `/api/summary?tenant=` | Inventory/risk counts and rolling-origin validation metrics |
| `/api/resources?tenant=&resource_type=&rack=&risk=&q=&sort=&limit=&offset=` | Filterable/sortable resource inventory |
| `/api/resources/{id}/forecast?tenant=` | Forecast, last 150 observations, chart path/bands, attribution, changes/events and audit row |
| `/api/alerts?tenant=&limit=` | Risk-ordered warnings with evidence and advisory recommendation |
| `/api/events?tenant=&resource_id=` | Logged events in the selected scope |
| `/api/audit?tenant=&resource_id=&limit=` | Forecast audit history |
| `/api/validation?tenant=` | Rolling-origin backtest and measured warning statistics |
| `/api/tenants` | Demo tenant names; there is no authentication |
| `/api/notifications/status` | Delivery configuration and most recent scheduler result (channel names only; ntfy topic is shown for operator setup, never tokens) |
| `/api/notifications?tenant=&resource_id=&limit=` | Notification delivery history, scoped by tenant when selected |

When a tenant is supplied, all scoped computations use only that tenant's telemetry, events and audit scope. Unknown tenants and resources with no telemetry for that tenant return 404 rather than disclosing whether a resource exists. This models the intended boundary but is not security; the app has no authenticated identity.

## Data cleaning and forecast method

The loader removes duplicate `(timestamp, resource_id, tenant_id)` rows (keeps last), drops/counts missing or negative measurements, pivots by tenant, and interpolates only interior gaps of at most three consecutive days. Cleaner counts and latest data time are exposed at `/api/dataset`. No long gaps are filled. Missing required telemetry columns raise an explicit error.

For each resource, the deterministic engine:

1. Uses data no later than its `as_of` date. A causal seven-day median and rolling robust MAD identify anomalous groups; temporary and unconfirmed spikes are median-replaced for fitting, while persistent level shifts remain.
2. Compares OLS slopes in adjacent 14-day periods every three days, and requires a four-combined-standard-error difference plus a 20% minimum change. Forecast window is 60 days, shortened after a recent change (minimum 14).
3. Compares rolling temporal holdout MAE for windowed OLS and damped Holt (alpha .3, beta .1, phi .98). A statistically supported positive OLS slope is required to estimate threshold timing; current values at/above the safety threshold are immediately due. A crossing outside the 365-day projection horizon is left unestimated. The physical limit is separately disclosed.
4. Reports a 95% slope-based day range and 30/60/90-day value intervals. The confidence label reflects range width. Thermal seasonality is not modelled; unsupported trends do not receive invented dates.
5. Associates positive per-tenant changes over the prior 30 days and matching logged events. Attribution is explicitly **association only, causation not established**; if no tenant grew, the cause is unknown.

The validation endpoint performs rolling-origin forecasts at seven-day steps. It reports 30-day MAE/RMSE for the chosen, OLS, damped Holt and last-value models, and warning outcomes only where a 90-day future is observable. Results are limited to this synthetic series, its planted events and tested periods; they do not prove general forecast accuracy or reduced outages.

## Audit and safety

`data/audit.db` stores a row for each resource at initial app startup and each resource forecast request: UTC run time, resource and tenant scope, model/version, input date range, first 16 hex characters of SHA-256 over scoped input values, status, days remaining and result JSON. Existing history is preserved. The audit is a prototype SQLite file; it is not tamper-proof.

All recommendations are advisory. The app contains no control-plane integration and never executes remediation. Hardware lead time (6–10 weeks) is contextual only.

## Mobile notifications

The backend periodically evaluates the same forecast and alert logic used by the dashboard. It sends one digest for a batch, and individual messages for Critical resources. New Watch/High Risk/Critical alerts, escalations, 30/14/7-day lead-time crossings and materially changed recent regimes can notify. Unconfirmed spikes are suppressed by default. Repeats respect the configured cooldown; optional recovery messages can be enabled with `NOTIFY_SEND_RESOLVED=true`.

### ntfy on Android or iOS

1. Install the free **ntfy** app from Google Play (Android) or the App Store (iOS).
2. Generate a long random topic:

   **Windows PowerShell**
   ```powershell
   python -c "import secrets; print('capacity-' + secrets.token_urlsafe(16))"
   ```

   **macOS/Linux**
   ```bash
   python3 -c "import secrets; print('capacity-' + secrets.token_urlsafe(16))"
   ```

3. Copy `.env.example` to `.env` and set `NTFY_TOPIC` to the generated value. Keep the topic private; do not commit `.env`.

   **Windows PowerShell**
   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   **macOS/Linux**
   ```bash
   cp .env.example .env
   nano .env
   ```

4. In the ntfy app, subscribe to the exact same topic. Restart the backend so it loads the configuration.
5. Run `python -m backend.notify --test` from the project root and confirm the test notification reaches the phone. For a local-only check use `python -m backend.notify --dry-run`.
6. Start the backend normally. Its background check runs every `NOTIFY_INTERVAL_MINUTES` (15 by default); use `python -m backend.notify --run-once` for an immediate evaluation. `python -m backend.notify --simulate-escalation` replays a historical-to-current synthetic alert in dry-run mode for a demo.

The phone needs internet access to receive remote notifications. A public `ntfy.sh` topic is only as private as its name: anyone who learns the topic can subscribe or publish. Choose a long random name, or use a self-hosted server and an access token. Notification messages pass through the configured third-party server (and the mobile push infrastructure). Keep tenant-sensitive details minimal. Optional Telegram uses `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; optional SMTP uses `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `EMAIL_TO`. Secrets are read from the environment or local `.env` and never written to notification logs.

`NOTIFY_TENANT_TOPICS` can map demo tenants to separate ntfy topics (`acme:topicA,globex:topicB`). Tenant-specific cycles use only their own telemetry and only the mapped ntfy topic (or local console fallback). The automatic background cycle is operator-scoped. The tenant selector and topics do not provide authentication or a secure identity boundary; there is still no authentication. Operator digests go only to operator-configured channels.

The dashboard's **Notifications** page shows configured channel names, filters, cooldown, last cycle result and delivery history. It displays the operator ntfy topic only in Operator view and never displays tokens.

## Tests and build

```powershell
python -m pytest -q
cd frontend
npm run build
```

## Known limitations

- No authentication or production security; tenant selection is a demo-only simulation.
- Synthetic data has few events and is not representative evidence for operational performance.
- Seasonality, holidays, dependencies between facility resources and cross-resource constraints are not modelled.
- Forecast uncertainty uses a simple OLS slope interval and residual band; it is not a calibrated probabilistic guarantee.
- Audit records are local SQLite entries and are not immutable.
- Supplied CSV input is expected to follow the documented schemas and to be trusted by the operator.

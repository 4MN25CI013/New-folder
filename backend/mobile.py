"""
Mobile Storage Integration Module for Capacity Intelligence Backend
Detects connected phone storage (ADB / MTP), tracks capacity metrics,
and triggers the on-screen modal alert on the phone.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
THRESHOLD_PERCENT = 85.0
SERVER_PORT = 8765

POWERSHELL_DETECT_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$results = @()

try {
    $shell = New-Object -ComObject Shell.Application
    $thisPC = $shell.Namespace(17) # ssfDRIVES
    if ($thisPC) {
        foreach ($device in $thisPC.Items()) {
            if ($device.IsFolder -and $device.Path -notmatch '^[A-Z]:\\$') {
                $deviceFolder = $device.GetFolder
                $subCount = if ($deviceFolder) { $deviceFolder.Items().Count } else { 0 }
                
                if ($subCount -eq 0) {
                    $results += [PSCustomObject]@{
                        DeviceName   = [string]$device.Name
                        StorageName  = "Locked / Charging Only"
                        CapacityGB   = 0
                        UsedGB       = 0
                        FreeGB       = 0
                        PercentUsed  = 0
                        DeviceType   = "MTP_Locked"
                    }
                } else {
                    foreach ($storage in $deviceFolder.Items()) {
                        $freeBytes = $storage.ExtendedProperty("FreeSpace")
                        $capacityBytes = $storage.ExtendedProperty("Capacity")
                        
                        if (-not $capacityBytes -or $capacityBytes -eq 0) {
                            for ($i = 0; $i -lt 300; $i++) {
                                $header = $deviceFolder.GetDetailsOf($null, $i)
                                $val = $deviceFolder.GetDetailsOf($storage, $i)
                                if ($header -match 'Free space' -and $val -and -not $freeBytes) {
                                    if ($val -match '([0-9.,]+)\s*GB') { $freeBytes = [double]($matches[1].Replace(',', '')) * 1GB }
                                    elseif ($val -match '([0-9.,]+)\s*MB') { $freeBytes = [double]($matches[1].Replace(',', '')) * 1MB }
                                }
                                if (($header -match 'Total size' -or $header -match 'Capacity') -and $val -and -not $capacityBytes) {
                                    if ($val -match '([0-9.,]+)\s*GB') { $capacityBytes = [double]($matches[1].Replace(',', '')) * 1GB }
                                    elseif ($val -match '([0-9.,]+)\s*MB') { $capacityBytes = [double]($matches[1].Replace(',', '')) * 1MB }
                                }
                            }
                        }

                        if ($capacityBytes -and $capacityBytes -gt 0) {
                            $usedBytes = $capacityBytes - $freeBytes
                            $results += [PSCustomObject]@{
                                DeviceName   = [string]$device.Name
                                StorageName  = [string]$storage.Name
                                CapacityGB   = [math]::Round($capacityBytes / 1GB, 2)
                                UsedGB       = [math]::Round($usedBytes / 1GB, 2)
                                FreeGB       = [math]::Round($freeBytes / 1GB, 2)
                                PercentUsed  = [math]::Round(($usedBytes / $capacityBytes) * 100, 1)
                                DeviceType   = "MTP_Phone"
                            }
                        }
                    }
                }
            }
        }
    }
} catch {}

$results | ConvertTo-Json -Compress
"""


def get_adb_path() -> str | None:
    local_adb = ROOT_DIR / "tools" / "platform-tools" / "adb.exe"
    if local_adb.is_file():
        return str(local_adb)
    system_adb = shutil.which("adb")
    return system_adb if system_adb else None


def get_connected_mobile_storage() -> dict[str, Any]:
    """Scans for connected mobile device and returns storage metrics."""
    adb = get_adb_path()

    # 1. Try ADB first
    if adb:
        try:
            check = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=6)
            lines = [l.strip() for l in check.stdout.splitlines() if l.strip() and not l.startswith("List")]
            devices = [l.split()[0] for l in lines if len(l.split()) >= 2 and l.split()[1] == "device"]

            for dev_serial in devices:
                model_proc = subprocess.run(
                    [adb, "-s", dev_serial, "shell", "getprop", "ro.product.model"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                raw_model = model_proc.stdout.strip()
                if "CPH2095" in raw_model:
                    model_name = "OPPO F17 (CPH2095)"
                elif raw_model:
                    model_name = raw_model
                else:
                    model_name = f"Android ({dev_serial})"

                df_proc = subprocess.run(
                    [adb, "-s", dev_serial, "shell", "df", "/data"],
                    capture_output=True,
                    text=True,
                    timeout=6,
                )
                match = re.search(r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%\s+/data", df_proc.stdout)
                if match:
                    total_1k = float(match.group(1))
                    used_1k = float(match.group(2))
                    free_1k = float(match.group(3))
                    pct = float(match.group(4))

                    cap_gb = round(total_1k / (1024 * 1024), 2)
                    used_gb = round(used_1k / (1024 * 1024), 2)
                    free_gb = round(free_1k / (1024 * 1024), 2)
                    is_critical = pct >= THRESHOLD_PERCENT

                    return {
                        "connected": True,
                        "device_name": model_name,
                        "storage_name": "Internal Storage",
                        "capacity_gb": cap_gb,
                        "used_gb": used_gb,
                        "free_gb": free_gb,
                        "percent_used": pct,
                        "threshold": THRESHOLD_PERCENT,
                        "is_critical": is_critical,
                        "status": "Critical" if is_critical else "Healthy",
                        "connection_mode": "ADB (Direct USB)",
                        "serial": dev_serial,
                    }
        except Exception:
            pass

    # 2. Try Windows Shell MTP
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", POWERSHELL_DETECT_SCRIPT],
            capture_output=True,
            text=True,
            timeout=8,
        )
        output = proc.stdout.strip()
        if output:
            data = json.loads(output)
            items = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for item in items:
                if item.get("DeviceType") != "MTP_Locked" and item.get("CapacityGB", 0) > 0:
                    pct = item["PercentUsed"]
                    is_critical = pct >= THRESHOLD_PERCENT
                    return {
                        "connected": True,
                        "device_name": item["DeviceName"],
                        "storage_name": item["StorageName"],
                        "capacity_gb": item["CapacityGB"],
                        "used_gb": item["UsedGB"],
                        "free_gb": item["FreeGB"],
                        "percent_used": pct,
                        "threshold": THRESHOLD_PERCENT,
                        "is_critical": is_critical,
                        "status": "Critical" if is_critical else "Healthy",
                        "connection_mode": "MTP (Storage Device)",
                        "serial": None,
                    }
                elif item.get("DeviceType") == "MTP_Locked":
                    return {
                        "connected": True,
                        "device_name": item["DeviceName"],
                        "storage_name": "Locked / Charging Only",
                        "capacity_gb": 0,
                        "used_gb": 0,
                        "free_gb": 0,
                        "percent_used": 0,
                        "threshold": THRESHOLD_PERCENT,
                        "is_critical": False,
                        "status": "Locked",
                        "connection_mode": "MTP (Locked)",
                        "notice": "Phone is set to 'Charging Only'. Please unlock screen and select 'Transfer Files'.",
                        "serial": None,
                    }
    except Exception:
        pass

    return {
        "connected": False,
        "device_name": None,
        "storage_name": None,
        "capacity_gb": 0,
        "used_gb": 0,
        "free_gb": 0,
        "percent_used": 0,
        "threshold": THRESHOLD_PERCENT,
        "is_critical": False,
        "status": "Disconnected",
        "connection_mode": "None",
        "notice": "No mobile storage device connected.",
    }


def trigger_mobile_popup(status_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Triggers the on-screen modal pop-up dialog directly on the phone and PC."""
    data = status_data or get_connected_mobile_storage()
    adb = get_adb_path()

    triggered_phone = False
    triggered_pc = False

    # 1. Trigger on Phone via ADB
    if adb and data.get("connected"):
        serial = data.get("serial")
        target_args = [adb, "-s", serial] if serial else [adb]
        try:
            # Wake screen
            subprocess.run(target_args + ["shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True, timeout=5)
            # Setup reverse port
            subprocess.run(target_args + ["reverse", "tcp:8000", "tcp:8000"], capture_output=True, timeout=5)
            # Launch modal web dialog
            subprocess.run(
                target_args + ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "http://localhost:8000/api/mobile/modal"],
                capture_output=True,
                timeout=5,
            )
            # Pull down notification shade
            title = f"⚠️ Storage Alert: {data['percent_used']}% Full!"
            body = f"Storage has exceeded 85%! Used: {data['used_gb']} GB / {data['capacity_gb']} GB ({data['free_gb']} GB free)."
            subprocess.run(target_args + ["shell", "cmd", "notification", "post", "-S", "bigtext", "-t", title, "cap_alert", body], capture_output=True, timeout=5)
            subprocess.run(target_args + ["shell", "cmd", "statusbar", "expand-notifications"], capture_output=True, timeout=5)
            triggered_phone = True
        except Exception:
            pass

    # 2. Trigger on PC screen
    def _pc_popup():
        title = f"\"Storage\" Critical Warning"
        msg = (
            f"\"Storage\" Would Like to Alert You\n\n"
            f"Device: {data.get('device_name', 'Mobile Phone')}\n"
            f"Storage usage: {data.get('percent_used', 0)}% (Exceeds 85% limit)\n"
            f"Used: {data.get('used_gb', 0)} GB / {data.get('capacity_gb', 0)} GB\n"
            f"Free Space: {data.get('free_gb', 0)} GB remaining.\n\n"
            f"Please clean up files to free up space."
        )
        try:
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0x30 | 0x1 | 0x10000)
        except Exception:
            pass

    threading.Thread(target=_pc_popup, daemon=True).start()
    triggered_pc = True

    return {
        "success": True,
        "triggered_phone": triggered_phone,
        "triggered_pc": triggered_pc,
        "device": data,
    }


def get_modal_html(data: dict[str, Any]) -> str:
    """Returns the standalone HTML matching the user's uploaded modal pop-up image."""
    device_name = data.get("device_name") or "Mobile Phone"
    used_gb = data.get("used_gb", 95.61)
    cap_gb = data.get("capacity_gb", 108.02)
    free_gb = data.get("free_gb", 12.41)
    pct = data.get("percent_used", 88.5)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Storage Warning</title>
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      -webkit-tap-highlight-color: transparent;
    }}
    body {{
      background: rgba(0, 0, 0, 0.55);
      height: 100vh;
      width: 100vw;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      overflow: hidden;
    }}
    .modal-box {{
      background: rgba(255, 255, 255, 0.97);
      backdrop-filter: blur(25px);
      -webkit-backdrop-filter: blur(25px);
      border-radius: 18px;
      width: 100%;
      max-width: 320px;
      text-align: center;
      box-shadow: 0 25px 60px rgba(0, 0, 0, 0.45);
      overflow: hidden;
      animation: popIn 0.22s cubic-bezier(0.18, 0.89, 0.32, 1.28);
    }}
    @keyframes popIn {{
      from {{ transform: scale(0.82); opacity: 0; }}
      to {{ transform: scale(1); opacity: 1; }}
    }}
    .modal-content {{
      padding: 26px 20px 20px;
    }}
    .modal-title {{
      font-size: 18px;
      font-weight: 700;
      color: #111;
      margin-bottom: 10px;
      letter-spacing: -0.3px;
      line-height: 1.3;
    }}
    .modal-message {{
      font-size: 14px;
      color: #333;
      line-height: 1.45;
      margin-bottom: 14px;
    }}
    .modal-badge {{
      display: inline-block;
      background: #fee2e2;
      color: #b91c1c;
      font-weight: 700;
      font-size: 13px;
      padding: 7px 14px;
      border-radius: 10px;
      border: 1px solid #fecaca;
    }}
    .modal-actions {{
      display: flex;
      border-top: 0.5px solid rgba(60, 60, 67, 0.29);
    }}
    .modal-btn {{
      flex: 1;
      padding: 14px 10px;
      font-size: 16.5px;
      font-weight: 600;
      border: none;
      background: transparent;
      cursor: pointer;
      color: #007aff;
      outline: none;
      text-decoration: none;
      display: block;
    }}
    .modal-btn.secondary {{
      border-right: 0.5px solid rgba(60, 60, 67, 0.29);
      font-weight: 400;
      color: #007aff;
    }}
    .modal-btn:active {{
      background: rgba(0, 0, 0, 0.08);
    }}
  </style>
</head>
<body>

  <div class="modal-box">
    <div class="modal-content">
      <div class="modal-title">"Storage" Would Like to Alert You</div>
      <div class="modal-message">
        Storage on {device_name} has exceeded 85%. You have {used_gb} GB used out of {cap_gb} GB ({free_gb} GB free).
      </div>
      <div class="modal-badge">
        Usage: {pct}% (Critical Limit)
      </div>
    </div>
    <div class="modal-actions">
      <a class="modal-btn secondary" href="javascript:window.close()">Don't Allow</a>
      <a class="modal-btn" href="/api/mobile/open-storage">OK</a>
    </div>
  </div>

</body>
</html>
"""


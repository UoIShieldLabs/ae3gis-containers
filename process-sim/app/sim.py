#!/usr/bin/env python3
"""Physical-process simulator + projector view for the AE3GIS lab.

Polls the field device's true output and shows it on a big web page (:8000), so a
demo can project the real process state next to the HMI. One image serves both
scenarios, chosen by SIM_PROTO:
  - modbus : read a register/coil from the PLC (OpenPLC, Scenario A)
  - iec104 : read the breaker + current/voltage from the RTU (c104-server, Sc. B)

For Scenario B the RTU is the single source of telemetry: this projector reads the
breaker state (M_SP) and the derived line-current / bus-voltage measured values
(M_ME) straight from the field device. CLOSED -> ENERGIZED with the RTU's reported
current/voltage; OPEN -> DARK, everything zero. If an M_ME point is unavailable it
falls back to a nominal constant so the view still renders.

Also exposes a terminal readout: `feeder` (or `python3 sim.py status`) prints the
same energized/dark . % . A . kV line for a CLI "god-view". Educational, isolated-lab use.
"""
import os
import socket
import sys
import threading
import time

PROTO = os.environ.get("SIM_PROTO", "modbus").lower()
WEB_PORT = int(os.environ.get("SIM_WEB_PORT", "8000"))
FIELD_HOST = os.environ.get("FIELD_HOST", "openplc")
POLL_SECONDS = float(os.environ.get("SIM_POLL_SECONDS", "2"))

# Modbus (Scenario A) knobs
MODBUS_PORT = int(os.environ.get("FIELD_PORT", "502"))
MODBUS_ADDRESS = int(os.environ.get("MODBUS_ADDRESS", "0"))
MODBUS_KIND = os.environ.get("MODBUS_KIND", "holding")  # holding | coil

# IEC-104 (Scenario B) knobs
IEC104_PORT = int(os.environ.get("FIELD_PORT", "2404"))
IEC104_COMMON_ADDRESS = int(os.environ.get("IEC104_COMMON_ADDRESS", "47"))
IEC104_STATE_IOA = int(os.environ.get("BREAKER_STATE_IOA", "11"))
IEC104_CURRENT_IOA = int(os.environ.get("FEEDER_CURRENT_IOA", "13"))
IEC104_VOLTAGE_IOA = int(os.environ.get("FEEDER_VOLTAGE_IOA", "14"))

# Fallback nominal readout (used when no measured value is available, e.g. Modbus
# or an RTU without M_ME points). Shown only while energized.
FEEDER_CURRENT_A = float(os.environ.get("FEEDER_CURRENT_A", "420"))
FEEDER_VOLTAGE_KV = float(os.environ.get("FEEDER_VOLTAGE_KV", "11.0"))

_lock = threading.Lock()
_state = {"breaker": None, "current": None, "voltage": None, "updated": 0.0, "ok": False}


def _set(breaker, current, voltage, ok: bool) -> None:
    with _lock:
        if breaker is not None:
            _state["breaker"] = breaker
        _state["current"] = current
        _state["voltage"] = voltage
        _state["ok"] = ok
        if ok:
            _state["updated"] = time.time()


def poll_modbus() -> None:
    from pymodbus.client import ModbusTcpClient

    client = ModbusTcpClient(host=FIELD_HOST, port=MODBUS_PORT)
    while True:
        try:
            client.connect()
            if MODBUS_KIND == "coil":
                rr = client.read_coils(MODBUS_ADDRESS, count=1)
                value = None if rr.isError() else bool(rr.bits[0])
            else:
                rr = client.read_holding_registers(MODBUS_ADDRESS, count=1)
                value = None if rr.isError() else int(rr.registers[0])
            _set(value, None, None, value is not None)
        except Exception:
            _set(None, None, None, False)
        time.sleep(POLL_SECONDS)


def _resolve(host: str) -> str:
    """Return host as an IP, retrying while DNS/hosts is not ready yet (c104 needs a numeric IP)."""
    while True:
        try:
            return socket.gethostbyname(host)
        except OSError:
            time.sleep(POLL_SECONDS)


def poll_iec104() -> None:
    import c104

    client = c104.Client()
    connection = client.add_connection(ip=_resolve(FIELD_HOST), port=IEC104_PORT)
    station = connection.add_station(common_address=IEC104_COMMON_ADDRESS)
    breaker = station.add_point(io_address=IEC104_STATE_IOA, type=c104.Type.M_SP_NA_1)
    current = station.add_point(io_address=IEC104_CURRENT_IOA, type=c104.Type.M_ME_NC_1)
    voltage = station.add_point(io_address=IEC104_VOLTAGE_IOA, type=c104.Type.M_ME_NC_1)
    client.start()
    while True:
        try:
            if connection.is_connected:
                connection.interrogation(common_address=IEC104_COMMON_ADDRESS)
                b = breaker.value
                _set(None if b is None else bool(b), current.value, voltage.value,
                     b is not None)
            else:
                _set(None, None, None, False)
        except Exception:
            _set(None, None, None, False)
        time.sleep(POLL_SECONDS)


def _readout(breaker, current, voltage):
    """Map polled field values to (key, status, sub, powered, current_str, voltage_str)."""
    energized = breaker is True or (isinstance(breaker, (int, float))
                                    and not isinstance(breaker, bool) and breaker != 0)
    de_energized = breaker is False or (isinstance(breaker, (int, float))
                                        and not isinstance(breaker, bool) and breaker == 0)
    if energized:
        amps = current if isinstance(current, (int, float)) and current else FEEDER_CURRENT_A
        kv = voltage if isinstance(voltage, (int, float)) and voltage else FEEDER_VOLTAGE_KV
        return ("energized", "ENERGIZED", "feeder online",
                "100%", f"{amps:.0f} A", f"{kv:.1f} kV")
    if de_energized:
        return ("dark", "DARK", "feeder de-energized", "0%", "0 A", "0 kV")
    return ("unknown", "—", "awaiting field device", "—", "—", "—")


def _snapshot():
    with _lock:
        return (_state["breaker"], _state["current"], _state["voltage"],
                _state["updated"], _state["ok"])


# ── CLI readout: `python3 sim.py status` (and the `feeder` wrapper) ─────────────
def cli_status() -> int:
    """One-shot terminal readout of the feeder, for a god-view CLI cell."""
    if PROTO != "iec104":
        print("feeder readout is only available for SIM_PROTO=iec104", file=sys.stderr)
        return 2
    import c104

    client = c104.Client()
    conn = client.add_connection(ip=_resolve(FIELD_HOST), port=IEC104_PORT)
    station = conn.add_station(common_address=IEC104_COMMON_ADDRESS)
    breaker = station.add_point(io_address=IEC104_STATE_IOA, type=c104.Type.M_SP_NA_1)
    current = station.add_point(io_address=IEC104_CURRENT_IOA, type=c104.Type.M_ME_NC_1)
    voltage = station.add_point(io_address=IEC104_VOLTAGE_IOA, type=c104.Type.M_ME_NC_1)
    client.start()
    time.sleep(2)
    conn.interrogation(common_address=IEC104_COMMON_ADDRESS)
    time.sleep(1)
    _k, status, sub, powered, cur, volt = _readout(breaker.value, current.value, voltage.value)
    print(f"feeder: {status}  ({sub}) · powered {powered} · {cur} · {volt}")
    return 0


app = None
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in ("status", "feeder"):
    sys.exit(cli_status())

from flask import Flask  # noqa: E402  (deferred so the CLI path needs no Flask)

# A projector-friendly dark page: one big status word, three derived metrics, and
# a telemetry/freshness footer. No external assets (the page may be relayed through
# the backend web-UI proxy), so styling is inline. Values are substituted with
# str.replace to keep the CSS brace-free (like the HMI page).
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Substation feeder</title><meta http-equiv="refresh" content="2">
<style>
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:3vmin;font-family:system-ui,-apple-system,sans-serif;
    background:#0b1021;color:#e8eaf6}
  .status{font-size:16vmin;font-weight:800;line-height:1;letter-spacing:.02em;color:var(--c)}
  .sub{font-size:2.2vmin;letter-spacing:.32em;text-transform:uppercase;color:#9fa8da;margin-top:-2vmin}
  .metrics{display:flex;gap:7vmin;margin-top:1vmin}
  .metric{display:flex;flex-direction:column;align-items:center;gap:.6vmin}
  .m-val{font-size:6vmin;font-weight:700;color:var(--c)}
  .m-lab{font-size:1.7vmin;letter-spacing:.22em;text-transform:uppercase;color:#7d8ea3}
  .meta{margin-top:2vmin;font-size:1.6vmin;color:#6b7a90;font-family:ui-monospace,monospace;letter-spacing:.05em}
  .meta.stale{color:#ff8a80}
  .energized{--c:#2fd27a}
  .dark{--c:#ff5a5a}
  .unknown{--c:#9fb0c3}
</style></head>
<body class="__KEY__">
  <div class="status">__STATUS__</div>
  <div class="sub">__SUB__</div>
  <div class="metrics">
    <div class="metric"><div class="m-val">__POWERED__</div><div class="m-lab">Powered</div></div>
    <div class="metric"><div class="m-val">__CURRENT__</div><div class="m-lab">Line current</div></div>
    <div class="metric"><div class="m-val">__VOLTAGE__</div><div class="m-lab">Bus voltage</div></div>
  </div>
  <div class="meta __STALECLS__">__PROTO__ · __HOST__ · __FRESH__</div>
</body></html>"""

app = Flask(__name__)


@app.route("/")
def index() -> str:
    breaker, current, voltage, updated, ok = _snapshot()
    key, status, sub, powered, cur, volt = _readout(breaker, current, voltage)

    if not updated:
        freshness, stalecls = "no data yet", "stale"
    else:
        age = time.time() - updated
        fresh = ok and age < POLL_SECONDS * 3
        freshness = "live" if fresh else f"stale ({int(age)}s)"
        stalecls = "" if fresh else "stale"

    target = f"{FIELD_HOST}:{MODBUS_PORT if PROTO == 'modbus' else IEC104_PORT}"
    repl = {
        "__KEY__": key,
        "__STATUS__": status,
        "__SUB__": sub,
        "__POWERED__": powered,
        "__CURRENT__": cur,
        "__VOLTAGE__": volt,
        "__STALECLS__": stalecls,
        "__PROTO__": PROTO,
        "__HOST__": target,
        "__FRESH__": freshness,
    }
    page = PAGE
    for token, replacement in repl.items():
        page = page.replace(token, replacement)
    return page


if PROTO == "iec104":
    threading.Thread(target=poll_iec104, daemon=True).start()
else:
    threading.Thread(target=poll_modbus, daemon=True).start()

print(f"[process-sim] {PROTO} view on :{WEB_PORT}, field device {FIELD_HOST}", flush=True)
app.run(host="0.0.0.0", port=WEB_PORT)

#!/usr/bin/env python3
"""Physical-process simulator + projector view for the AE3GIS lab.

Polls the field device's true output and shows it on a big web page (:8000),
so a demo can project the real process state next to the HMI. One image serves
both scenarios, chosen by SIM_PROTO:
  - modbus : read a register/coil from the PLC (OpenPLC, Scenario A)
  - iec104 : read the breaker point from the RTU (c104-server, Scenario B)

For Scenario B the page renders a small substation readout derived from the
breaker state: when the breaker is CLOSED the feeder is ENERGIZED and shows
nominal powered%/current/voltage; when it is OPEN everything collapses to zero
(the city goes DARK). The electrical values are a simple illustrative model, not
a power-flow solver — nominal constants are env-overridable.

Built to stay up: every poll is guarded, and the view keeps the last known value
and marks it stale rather than crashing. Educational, isolated-lab use.
"""
import os
import socket
import threading
import time

from flask import Flask

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

# Derived electrical readout (illustrative). Shown when the feeder is energized;
# all values collapse to zero when the breaker opens.
FEEDER_CURRENT_A = float(os.environ.get("FEEDER_CURRENT_A", "420"))
FEEDER_VOLTAGE_KV = float(os.environ.get("FEEDER_VOLTAGE_KV", "11.0"))

_lock = threading.Lock()
_state = {"value": None, "updated": 0.0, "ok": False}


def _set(value, ok: bool) -> None:
    with _lock:
        if value is not None:
            _state["value"] = value
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
            _set(value, value is not None)
        except Exception:
            _set(None, False)
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
    point = station.add_point(io_address=IEC104_STATE_IOA, type=c104.Type.M_SP_NA_1)
    client.start()
    while True:
        try:
            if connection.is_connected:
                connection.interrogation(common_address=IEC104_COMMON_ADDRESS)
                val = point.value
                _set(None if val is None else bool(val), val is not None)
            else:
                _set(None, False)
        except Exception:
            _set(None, False)
        time.sleep(POLL_SECONDS)


app = Flask(__name__)

# A projector-friendly dark page: one big status word, three derived metrics, and
# a telemetry/freshness footer. No external assets (the page may be relayed
# through the backend web-UI proxy), so styling is inline. Values are substituted
# with str.replace to keep the CSS brace-free (like the HMI page).
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


def _readout(value):
    """Map the polled field value to (key, status, sub, powered, current, voltage)."""
    energized = value is True or (isinstance(value, (int, float))
                                  and not isinstance(value, bool) and value != 0)
    de_energized = value is False or (isinstance(value, (int, float))
                                      and not isinstance(value, bool) and value == 0)
    if energized:
        return ("energized", "ENERGIZED", "feeder online",
                "100%", f"{FEEDER_CURRENT_A:.0f} A", f"{FEEDER_VOLTAGE_KV:.1f} kV")
    if de_energized:
        return ("dark", "DARK", "feeder de-energized", "0%", "0 A", "0 kV")
    return ("unknown", "—", "awaiting field device", "—", "—", "—")


@app.route("/")
def index() -> str:
    with _lock:
        value, updated, ok = _state["value"], _state["updated"], _state["ok"]

    key, status, sub, powered, current, voltage = _readout(value)

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
        "__CURRENT__": current,
        "__VOLTAGE__": voltage,
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

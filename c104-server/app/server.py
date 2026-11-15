#!/usr/bin/env python3
"""Minimal IEC-104 RTU for the AE3GIS lab (Scenario B / Industroyer case study).

Exposes one substation breaker and its derived electrical measurements on TCP 2404:
  - M_SP_NA_1 (IOA 11) the breaker state,
  - C_SC_NA_1 (IOA 12) a single command that opens/closes it, and
  - M_ME_NC_1 (IOA 13 / 14) line current / bus voltage, derived from the breaker
    state (CLOSED -> nominal, OPEN -> 0), so an HMI sees the electrical quantities
    collapse on a trip, not just a flag. Nominal values are env-overridable.

The RTU is the single source of telemetry: the HMI and the process-sim projector
both read these points. A received command is mirrored onto the state + measured
points so every observer sees the change. Educational, isolated-lab use.
"""
import os
import signal
import time

import c104

PORT = int(os.environ.get("IEC104_PORT", "2404"))
COMMON_ADDRESS = int(os.environ.get("IEC104_COMMON_ADDRESS", "47"))
STATE_IOA = int(os.environ.get("BREAKER_STATE_IOA", "11"))
CMD_IOA = int(os.environ.get("BREAKER_CMD_IOA", "12"))
CURRENT_IOA = int(os.environ.get("FEEDER_CURRENT_IOA", "13"))
VOLTAGE_IOA = int(os.environ.get("FEEDER_VOLTAGE_IOA", "14"))

# Nominal electrical values while the feeder is energized (illustrative, not a
# power-flow solver). Collapse to zero when the breaker opens.
NOMINAL_CURRENT = float(os.environ.get("FEEDER_CURRENT_A", "420"))
NOMINAL_VOLTAGE = float(os.environ.get("FEEDER_VOLTAGE_KV", "11.0"))

server = c104.Server(ip="0.0.0.0", port=PORT)
station = server.add_station(common_address=COMMON_ADDRESS)

breaker = station.add_point(io_address=STATE_IOA, type=c104.Type.M_SP_NA_1)
command = station.add_point(io_address=CMD_IOA, type=c104.Type.C_SC_NA_1)
current = station.add_point(io_address=CURRENT_IOA, type=c104.Type.M_ME_NC_1)
voltage = station.add_point(io_address=VOLTAGE_IOA, type=c104.Type.M_ME_NC_1)


def apply_physics(closed: bool) -> None:
    """Breaker position drives the derived measurements."""
    breaker.value = closed
    current.value = NOMINAL_CURRENT if closed else 0.0
    voltage.value = NOMINAL_VOLTAGE if closed else 0.0


apply_physics(True)  # start closed / energized


def on_command(point: c104.Point, previous_info: c104.Information,
               message: c104.IncomingMessage) -> c104.ResponseState:
    closed = bool(point.value)
    apply_physics(closed)
    print(f"[c104-server] breaker -> {'CLOSED' if closed else 'OPEN'} "
          f"({current.value:.0f} A, {voltage.value:.1f} kV)", flush=True)
    return c104.ResponseState.SUCCESS


command.on_receive(callable=on_command)

server.start()
print(f"[c104-server] IEC-104 RTU listening on :{PORT} "
      f"(CA={COMMON_ADDRESS}, state IOA={STATE_IOA}, command IOA={CMD_IOA}, "
      f"current IOA={CURRENT_IOA}, voltage IOA={VOLTAGE_IOA})",
      flush=True)

signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
while True:
    time.sleep(1)

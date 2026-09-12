"""One explicit, read-only Modbus TCP request; no scans and no write functions.

Example (documentation-only IP):
  python tools/modbus_probe.py --host 192.0.2.10 --unit 1 --address 0x1001

Use an address from the documentation for the exact charger model. A Witty Plus
register map is not proof of compatibility with Witty Solar. The tool never
changes the existing energy manager, fallback settings or charging current.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import struct
from typing import Any


class ModbusProbeError(Exception):
    """Malformed or unexpected Modbus response."""


def build_read_request(unit: int, address: int, count: int, transaction: int = 1) -> bytes:
    """FC03 only. Unit 0 is permitted here only because this is a read."""
    values = (unit, address, count, transaction)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("All request fields must be integers")
    if not 0 <= unit <= 255 or not 0 <= transaction <= 65535:
        raise ValueError("Invalid unit or transaction identifier")
    if not 1 <= count <= 16 or not 0 <= address <= 65536 - count:
        raise ValueError("Read 1-16 registers within the 16-bit address space")
    return struct.pack(">HHHBBHH", transaction, 0, 6, unit, 3, address, count)


def read_exact(stream: socket.socket, count: int) -> bytes:
    result = bytearray()
    while len(result) < count:
        chunk = stream.recv(count - len(result))
        if not chunk:
            raise ModbusProbeError("Peer closed the connection before a complete reply")
        result.extend(chunk)
    return bytes(result)


def parse_reply(header: bytes, body: bytes, unit: int, count: int, transaction: int = 1) -> dict[str, Any]:
    if len(header) != 7:
        raise ModbusProbeError("Invalid MBAP header size")
    reply_id, protocol, length, reply_unit = struct.unpack(">HHHB", header)
    if reply_id != transaction or protocol != 0 or reply_unit != unit:
        raise ModbusProbeError("Response transaction, protocol or unit does not match")
    if not 3 <= length <= 254 or len(body) != length - 1:
        raise ModbusProbeError("Invalid or incomplete response length")
    if body[0] == 0x83 and len(body) == 2:
        return {"modbus_reply_valid": True, "registers_read": False, "exception_code": body[1]}
    if body[0] != 3 or body[1] != count * 2 or len(body) != 2 + count * 2:
        raise ModbusProbeError("Unexpected function or register count")
    return {
        "modbus_reply_valid": True,
        "registers_read": True,
        "registers": list(struct.unpack(">" + "H" * count, body[2:])),
    }


def probe(host: str, port: int = 502, unit: int = 1, address: int = 0x1001,
          count: int = 1, timeout: float = 2.0) -> dict[str, Any]:
    request = build_read_request(unit, address, count)
    if not host or not 1 <= port <= 65535 or not math.isfinite(timeout) or not 0 < timeout <= 10:
        raise ValueError("Invalid host, port or timeout (maximum 10 seconds)")
    result: dict[str, Any] = {
        "read_only": True, "function": 3, "unit": unit,
        "address": hex(address), "count": count,
        "tcp_connected": False, "modbus_reply_valid": False, "registers_read": False,
    }
    try:
        with socket.create_connection((host, port), timeout=timeout) as stream:
            result["tcp_connected"] = True
            stream.settimeout(timeout)
            stream.sendall(request)
            header = read_exact(stream, 7)
            reply_id, protocol, length, reply_unit = struct.unpack(">HHHB", header)
            if reply_id != 1 or protocol != 0 or reply_unit != unit or not 3 <= length <= 254:
                raise ModbusProbeError("Invalid MBAP response")
            body = read_exact(stream, length - 1)
            result.update(parse_reply(header, body, unit, count))
    except (OSError, ModbusProbeError) as err:
        result["error"] = type(err).__name__ + ": " + str(err)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="IP/hostname of the authorized charger")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x1001)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()
    try:
        result = probe(**vars(args))
    except ValueError as err:
        parser.error(str(err))
    print(json.dumps(result, indent=2))
    return 0 if result["registers_read"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only views of data already fetched by Hager Local.

No network, account access, charging commands or device configuration writes.
"""
from __future__ import annotations

from datetime import time
import math
from typing import Any

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def finite_number(value: Any) -> float | None:
    """Do not turn missing, boolean or invalid readings into zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def nested(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def bounded_number(value: Any, minimum: float, maximum: float) -> float | None:
    result = finite_number(value)
    return result if result is not None and minimum <= result <= maximum else None


def charge_summary(configuration: dict) -> str:
    """Derived summary, not an undocumented mapping of numeric session codes."""
    if nested(configuration, "deviceState", "deviceConnected") is False:
        return "offline"
    if nested(configuration, "deviceState", "deviceWorking") is False:
        return "fault"
    if configuration.get("chargingActive") is True:
        return "charging"
    if configuration.get("type2Plugged") is True:
        return "connected_waiting"
    if configuration.get("type2Plugged") is False:
        return "disconnected"
    return "unknown"


def profile_summary(parameters: dict) -> str:
    sun = parameters.get("sunMode")
    if not isinstance(sun, dict):
        return "unknown"
    if sun.get("activated") is False:
        return "inactive"
    if sun.get("activated") is not True:
        return "unknown"
    return {"Disabled": "solar_only", "Immediate": "minimum", "Delayed": "delayed"}.get(sun.get("chargingStrategy"), "unknown")


def profile_days(parameters: dict) -> list[dict]:
    """Show only valid received/remembered days; no synthesized targets.

    The existing integration may hydrate this list from its profile cache.
    Callers must disclose that it is not necessarily a live acknowledged plan.
    """
    rows = nested(parameters, "sunMode", "parameterList")
    if not isinstance(rows, list) or len(rows) != 7:
        return []
    result = []
    for day, row in zip(DAYS, rows, strict=True):
        if not isinstance(row, dict):
            return []
        amount = bounded_number(row.get("minEnergy"), 0, 300)
        seconds = bounded_number(row.get("daytime"), 0, 86399)
        departure = None
        if seconds is not None and seconds.is_integer():
            value = int(seconds)
            departure = time(value // 3600, value % 3600 // 60, value % 60).isoformat()
        result.append({"day": day, "energy_kwh": amount, "departure": departure})
    return result


def public_diagnostic_view(configuration: dict, parameters: dict) -> dict:
    """Allow-list numeric/boolean fields, never arbitrary vendor payloads."""
    numeric = (
        "wallboxType", "pmPowerL1", "pmPowerL2", "pmPowerL3",
        "pmEnergyL1", "pmEnergyL2", "pmEnergyL3", "upperCurrentLimit",
        "lowerCurrentLimit", "maxChargeCurrent", "minChargeCurrent",
        "diagPhaseVoltage", "keyState", "status", "sessionStatus",
    )
    boolean = (
        "chargingActive", "type2Plugged", "maxChargeCurrentWatingFor",
        "rfidReaderEnabled", "authRequired", "autoPhaseSwitchEnabled",
        "cableLock", "chargingCanceled", "chargeInFallbackModeAllowed",
    )
    return {
        "readings": {key: finite_number(configuration.get(key)) for key in numeric},
        "flags": {key: flag(configuration.get(key)) for key in boolean},
        "device_connected": flag(nested(configuration, "deviceState", "deviceConnected")),
        "device_working": flag(nested(configuration, "deviceState", "deviceWorking")),
        "flow_settings": {key: finite_number(parameters.get(key)) for key in ("minCurrent", "fallbackMaxCurrent", "minCurrentSunMode")},
        "charge_summary": charge_summary(configuration),
        "profile_mode": profile_summary(parameters),
        "profile_received_or_cached": profile_days(parameters),
        "profile_may_be_cached": True,
    }

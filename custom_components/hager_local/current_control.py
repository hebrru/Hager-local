"""Conservative current-limit validation and confirmed Flow cloud writes.

This module has no Home Assistant dependency and never speaks Modbus. A cloud
readback confirms the reported setting, not the current actually drawn by a car.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import math
from typing import Any

MIN_CURRENT_A = 6
MAX_CURRENT_A = 32
SUPPORTED_WALLBOX_TYPE = 6  # Witty Solar; do not infer support for other models.


class CurrentControlError(Exception):
    """The current limit cannot be safely set or confirmed."""


@dataclass(frozen=True, slots=True)
class CurrentBounds:
    """Integer limits constrained by the live installer configuration."""

    minimum: int
    maximum: int


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CurrentControlError("Missing or invalid current-limit data")
    try:
        number = float(value)
    except (ValueError, OverflowError) as err:
        raise CurrentControlError("Missing or invalid current-limit data") from err
    if not math.isfinite(number):
        raise CurrentControlError("Current-limit data must be finite")
    return number


def current_bounds(configuration: Mapping[str, Any]) -> CurrentBounds:
    """Never invent a 32 A installer limit when the actual limit is absent."""
    if _finite_number(configuration.get("wallboxType")) != SUPPORTED_WALLBOX_TYPE:
        raise CurrentControlError("Current control is only enabled for Witty Solar type 6")
    upper = _finite_number(configuration.get("upperCurrentLimit"))
    lower = _finite_number(configuration.get("lowerCurrentLimit"))
    minimum = _finite_number(configuration.get("minChargeCurrent"))
    if min(upper, lower, minimum) <= 0:
        raise CurrentControlError("Live installer current limits must be positive")
    bounds = CurrentBounds(
        minimum=math.ceil(max(MIN_CURRENT_A, lower, minimum)),
        maximum=math.floor(min(MAX_CURRENT_A, upper)),
    )
    if bounds.minimum > bounds.maximum:
        raise CurrentControlError("Inconsistent live current limits; no write allowed")
    return bounds


def requested_current(value: Any) -> int:
    """Reject fractional, non-finite, boolean and out-of-range commands."""
    number = _finite_number(value)
    if not number.is_integer() or not MIN_CURRENT_A <= number <= MAX_CURRENT_A:
        raise CurrentControlError("Choose a whole number of amperes between 6 and 32")
    return int(number)


def configured_current(configuration: Mapping[str, Any]) -> int:
    """Read the reported setting without substituting a requested value."""
    bounds = current_bounds(configuration)
    value = requested_current(configuration.get("maxChargeCurrent"))
    if not bounds.minimum <= value <= bounds.maximum:
        raise CurrentControlError("Reported current is outside the installer limits")
    return value


def current_update_pending(configuration: Mapping[str, Any]) -> bool:
    """Hager's field name really contains the spelling 'WatingFor'."""
    pending = configuration.get("maxChargeCurrentWatingFor")
    if not isinstance(pending, bool):
        raise CurrentControlError("Current-setting acknowledgement is not available")
    return pending


def validate_live_configuration(configuration: Any) -> dict[str, Any]:
    """Require a fresh response and an online, working device before a write."""
    if not isinstance(configuration, dict):
        raise CurrentControlError("Live wallbox configuration unavailable; cached data cannot authorize a write")
    state = configuration.get("deviceState")
    if not isinstance(state, dict) or (
        state.get("deviceConnected") is not True
        or state.get("deviceWorking") is not True
    ):
        raise CurrentControlError("Wallbox is offline or not working; no write allowed")
    configured_current(configuration)
    current_update_pending(configuration)
    return configuration


async def async_set_confirmed_current(
    value: Any,
    *,
    read_configuration: Callable[[], Awaitable[dict[str, Any] | None]],
    write_limit: Callable[[int], Awaitable[None]],
    observe_configuration: Callable[[dict[str, Any]], None],
    confirmation_attempts: int = 5,
    confirmation_interval: float = 2.0,
    timeout: float = 40.0,
) -> dict[str, Any]:
    """Set only the operating limit and require a non-pending cloud readback.

    A rejected, timed-out or unconfirmed operation never fabricates success,
    never retries the write and never restores a higher previous current.
    The caller must serialize commands per wallbox.
    """
    target = requested_current(value)
    if not 1 <= confirmation_attempts <= 10:
        raise ValueError("confirmation_attempts must be between 1 and 10")
    if not math.isfinite(confirmation_interval) or confirmation_interval < 0:
        raise ValueError("confirmation_interval must be finite and non-negative")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    write_attempted = False
    try:
        async with asyncio.timeout(timeout):
            before = validate_live_configuration(await read_configuration())
            observe_configuration(before)
            bounds = current_bounds(before)
            if not bounds.minimum <= target <= bounds.maximum:
                raise CurrentControlError(
                    f"Requested current must stay between {bounds.minimum} and {bounds.maximum} A; "
                    "the installer limit will not be changed"
                )
            if current_update_pending(before):
                raise CurrentControlError("A Hager current update is already pending; wait and refresh")
            if configured_current(before) == target:
                return before  # No duplicate PUT, and no implicit start or boost.

            write_attempted = True
            await write_limit(target)
            for _ in range(confirmation_attempts):
                await asyncio.sleep(confirmation_interval)
                after = validate_live_configuration(await read_configuration())
                observe_configuration(after)
                if not current_update_pending(after) and configured_current(after) == target:
                    return after
            raise CurrentControlError(
                "Current command sent but not confirmed by Hager. Check the reported limit; "
                "the write was not repeated and no previous limit was restored"
            )
    except TimeoutError as err:
        raise CurrentControlError(
            "Current operation timed out; the final setting is uncertain. "
            "Refresh the wallbox before another command"
        ) from err
    except CurrentControlError as err:
        if write_attempted:
            raise CurrentControlError(
                "Current command not confirmed; it may have reached Hager. "
                "Check the reported setting before another command. "
                f"Readback detail: {err}"
            ) from err
        raise
    except Exception as err:
        if write_attempted:
            raise CurrentControlError(
                "Current command could not be confirmed; it may have reached Hager. "
                "Refresh the wallbox before another command"
            ) from err
        raise

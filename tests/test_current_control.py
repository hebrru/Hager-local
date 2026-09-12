"""Offline tests: no account, network, wallbox or Home Assistant required."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hager_current_control_test", ROOT / "custom_components/hager_local/current_control.py"
)
cc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cc
SPEC.loader.exec_module(cc)


def configuration(current=32, **updates):
    result = {
        "wallboxType": 6,
        "maxChargeCurrent": current,
        "minChargeCurrent": 6,
        "upperCurrentLimit": 32,
        "lowerCurrentLimit": 6,
        "maxChargeCurrentWatingFor": False,
        "deviceState": {"deviceConnected": True, "deviceWorking": True},
        "chargeFull": False,
        "sunModeActive": True,
    }
    result.update(updates)
    return result


class BoundsTests(unittest.TestCase):
    def test_valid_range(self):
        self.assertEqual(cc.current_bounds(configuration()), cc.CurrentBounds(6, 32))

    def test_installer_limit_is_authoritative(self):
        self.assertEqual(cc.current_bounds(configuration(16, upperCurrentLimit=16)), cc.CurrentBounds(6, 16))

    def test_supported_product_cap(self):
        self.assertEqual(cc.current_bounds(configuration(32, upperCurrentLimit=64)).maximum, 32)

    def test_higher_minimum_is_respected(self):
        self.assertEqual(cc.current_bounds(configuration(minChargeCurrent=10)).minimum, 10)

    def test_fractional_limits_round_inward(self):
        self.assertEqual(cc.current_bounds(configuration(16, lowerCurrentLimit=6.2, upperCurrentLimit=16.9)), cc.CurrentBounds(7, 16))

    def test_missing_limits_fail_closed(self):
        for key in ("wallboxType", "minChargeCurrent", "upperCurrentLimit", "lowerCurrentLimit"):
            with self.subTest(key=key), self.assertRaises(cc.CurrentControlError):
                data = configuration()
                del data[key]
                cc.current_bounds(data)

    def test_invalid_commands(self):
        for value in (None, True, False, 0, 5, -1, 33, 16.5, float("nan"), float("inf"), "nan", "bad", {}, []):
            with self.subTest(value=value), self.assertRaises(cc.CurrentControlError):
                cc.requested_current(value)

    def test_endpoints_are_accepted(self):
        for value in (6, 32, 16.0, "16"):
            self.assertEqual(cc.requested_current(value), int(float(value)))

    def test_invalid_limits(self):
        for value in (None, True, 0, -1, float("nan"), float("inf"), "bad"):
            with self.subTest(value=value), self.assertRaises(cc.CurrentControlError):
                cc.current_bounds(configuration(upperCurrentLimit=value))

    def test_inconsistent_limits(self):
        with self.assertRaises(cc.CurrentControlError):
            cc.current_bounds(configuration(minChargeCurrent=20, upperCurrentLimit=16))

    def test_unsupported_model(self):
        for value in (8, None, True, "unknown"):
            with self.subTest(value=value), self.assertRaises(cc.CurrentControlError):
                cc.current_bounds(configuration(wallboxType=value))

    def test_invalid_reported_current(self):
        with self.assertRaises(cc.CurrentControlError):
            cc.configured_current(configuration(32, upperCurrentLimit=16))

    def test_unknown_pending_is_not_false(self):
        for value in (None, 0, "false"):
            with self.subTest(value=value), self.assertRaises(cc.CurrentControlError):
                cc.current_update_pending(configuration(maxChargeCurrentWatingFor=value))

    def test_offline_or_faulted(self):
        for state in (None, {}, {"deviceConnected": False, "deviceWorking": True}, {"deviceConnected": True, "deviceWorking": False}):
            with self.subTest(state=state), self.assertRaises(cc.CurrentControlError):
                cc.validate_live_configuration(configuration(deviceState=state))


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, value, read, write=None, observe=None, **kwargs):
        self.write = write if write is not None else AsyncMock()
        self.observe = observe if observe is not None else Mock()
        return await cc.async_set_confirmed_current(
            value, read_configuration=read, write_limit=self.write,
            observe_configuration=self.observe, confirmation_interval=0, **kwargs,
        )

    async def test_reduce_confirmed(self):
        result = await self.invoke(16, AsyncMock(side_effect=[configuration(), configuration(16)]))
        self.write.assert_awaited_once_with(16)
        self.assertEqual(result["maxChargeCurrent"], 16)

    async def test_increase_confirmed(self):
        result = await self.invoke(32, AsyncMock(side_effect=[configuration(16), configuration(32)]))
        self.write.assert_awaited_once_with(32)
        self.assertEqual(result["maxChargeCurrent"], 32)

    async def test_same_value_does_not_write(self):
        read = AsyncMock(return_value=configuration(16))
        await self.invoke(16, read)
        self.write.assert_not_awaited()
        read.assert_awaited_once()

    async def test_pending_before_command_does_not_write(self):
        with self.assertRaises(cc.CurrentControlError):
            await self.invoke(16, AsyncMock(return_value=configuration(maxChargeCurrentWatingFor=True)))
        self.write.assert_not_awaited()

    async def test_live_none_cannot_be_replaced_by_cache(self):
        with self.assertRaises(cc.CurrentControlError):
            await self.invoke(16, AsyncMock(return_value=None))
        self.write.assert_not_awaited()

    async def test_over_installer_limit_does_not_write(self):
        with self.assertRaises(cc.CurrentControlError):
            await self.invoke(17, AsyncMock(return_value=configuration(16, upperCurrentLimit=16)))
        self.write.assert_not_awaited()

    async def test_invalid_command_does_not_even_read(self):
        read = AsyncMock()
        with self.assertRaises(cc.CurrentControlError):
            await self.invoke(33, read)
        read.assert_not_awaited()
        self.write.assert_not_awaited()

    async def test_pending_readback_is_not_confirmation(self):
        read = AsyncMock(side_effect=[configuration(), configuration(16, maxChargeCurrentWatingFor=True), configuration(16)])
        await self.invoke(16, read)
        self.assertEqual(read.await_count, 3)
        self.write.assert_awaited_once()

    async def test_ignored_put_never_claims_success_or_restores(self):
        read = AsyncMock(return_value=configuration())
        with self.assertRaisesRegex(cc.CurrentControlError, "not confirmed"):
            await self.invoke(16, read, confirmation_attempts=2)
        self.write.assert_awaited_once_with(16)
        self.assertTrue(all(call.args[0]["maxChargeCurrent"] == 32 for call in self.observe.call_args_list))

    async def test_pending_for_entire_readback_fails(self):
        read = AsyncMock(side_effect=[configuration(), configuration(16, maxChargeCurrentWatingFor=True)])
        with self.assertRaises(cc.CurrentControlError):
            await self.invoke(16, read, confirmation_attempts=1)
        self.write.assert_awaited_once()

    async def test_rejected_write_never_mutates_requested_state(self):
        before = configuration()
        original = deepcopy(before)
        write = AsyncMock(side_effect=OSError("synthetic failure"))
        with self.assertRaises(cc.CurrentControlError):
            await self.invoke(16, AsyncMock(return_value=before), write)
        self.assertEqual(before, original)
        write.assert_awaited_once()

    async def test_connection_lost_after_write_is_uncertain(self):
        read = AsyncMock(side_effect=[configuration(), OSError("synthetic network loss")])
        with self.assertRaisesRegex(cc.CurrentControlError, "may have reached"):
            await self.invoke(16, read)
        self.write.assert_awaited_once_with(16)

    async def test_timeout_before_write(self):
        async def read():
            await asyncio.sleep(1)
        with self.assertRaisesRegex(cc.CurrentControlError, "timed out"):
            await self.invoke(16, read, timeout=0.01)
        self.write.assert_not_awaited()

    async def test_timeout_during_write_does_not_retry(self):
        async def slow_write(value):
            await asyncio.sleep(1)
        write = AsyncMock(side_effect=slow_write)
        with self.assertRaisesRegex(cc.CurrentControlError, "timed out"):
            await self.invoke(16, AsyncMock(return_value=configuration()), write, timeout=0.01)
        write.assert_awaited_once()

    async def test_cancelled_write_is_not_retried(self):
        write = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke(16, AsyncMock(return_value=configuration()), write)
        write.assert_awaited_once()

    async def test_bounds_reduced_during_confirmation_fail(self):
        read = AsyncMock(side_effect=[configuration(), configuration(16, upperCurrentLimit=10)])
        with self.assertRaises(cc.CurrentControlError):
            await self.invoke(16, read)
        self.write.assert_awaited_once_with(16)

    async def test_success_preserves_other_settings(self):
        before, after = configuration(), configuration(16)
        await self.invoke(16, AsyncMock(side_effect=[before, after]))
        for key in ("upperCurrentLimit", "lowerCurrentLimit", "minChargeCurrent", "chargeFull", "sunModeActive"):
            self.assertEqual(before[key], after[key])


if __name__ == "__main__":
    unittest.main()

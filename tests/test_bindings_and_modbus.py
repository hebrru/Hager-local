"""Offline transport and HA entity binding tests with synthetic dependencies.

Selected production methods/classes are compiled from their AST. This exercises
our binding code, but is not a full Home Assistant runtime or a hardware test.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import asyncio
import importlib.util
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_current_control import ROOT, cc, configuration


class ApiError(Exception):
    pass


class HAError(Exception):
    pass


def load_class(path, name, namespace, methods=None):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    cls = deepcopy(next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name))
    if methods is not None:
        cls.body = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in methods]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


async def fast_confirmation(value, **kwargs):
    return await cc.async_set_confirmed_current(value, confirmation_interval=0, **kwargs)


class ApiBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cls = load_class(ROOT / "custom_components/hager_local/api.py", "HagerApiClient", {
            "asyncio": asyncio, "async_get_clientsession": lambda hass: object(),
            "HagerApiError": ApiError, "CurrentControlError": cc.CurrentControlError,
            "async_set_confirmed_current": fast_confirmation,
            "E3DC_AUTH_BASE_URL": "https://example.invalid",
        }, methods={"__init__", "async_set_max_charge_current"})
        self.api = cls(None, None)
        self.api._get_wallbox_configuration = AsyncMock(side_effect=[configuration(), configuration(16)])
        self.api._request_json = AsyncMock()
        self.wallbox = SimpleNamespace(emc_hardware_id="TEST-EMC", configuration_id="0", configuration={})

    async def test_only_operating_limit_is_sent(self):
        await self.api.async_set_max_charge_current(self.wallbox, 16)
        self.api._request_json.assert_awaited_once_with(
            "put", "https://example.invalid/wallboxes/TEST-EMC/0/configuration",
            json_payload={"maxChargeCurrent": 16}, expect_json=False, retry_on_auth_error=False,
        )
        self.assertEqual(self.wallbox.configuration["maxChargeCurrent"], 16)
        self.assertEqual(self.api._wallbox_configuration_cache[("TEST-EMC", "0")]["maxChargeCurrent"], 16)
        self.assertEqual(self.wallbox.configuration["upperCurrentLimit"], 32)

    async def test_busy_command_is_rejected_not_queued(self):
        lock = asyncio.Lock()
        self.api._current_limit_locks[("TEST-EMC", "0")] = lock
        await lock.acquire()
        try:
            with self.assertRaisesRegex(ApiError, "already running"):
                await self.api.async_set_max_charge_current(self.wallbox, 16)
        finally:
            lock.release()
        self.api._get_wallbox_configuration.assert_not_awaited()
        self.api._request_json.assert_not_awaited()

    async def test_cache_cannot_authorize_write(self):
        self.api._wallbox_configuration_cache[("TEST-EMC", "0")] = configuration()
        self.api._get_wallbox_configuration = AsyncMock(return_value=None)
        with self.assertRaises(ApiError):
            await self.api.async_set_max_charge_current(self.wallbox, 16)
        self.api._request_json.assert_not_awaited()
        self.assertFalse(self.api._current_limit_locks[("TEST-EMC", "0")].locked())

    async def test_rejected_write_does_not_publish_requested_value(self):
        self.api._request_json.side_effect = ApiError("synthetic rejection")
        with self.assertRaises(ApiError):
            await self.api.async_set_max_charge_current(self.wallbox, 16)
        self.assertEqual(self.wallbox.configuration["maxChargeCurrent"], 32)
        self.api._request_json.assert_awaited_once()


class BaseEntity:
    def __init__(self, coordinator, key):
        self.coordinator = coordinator
        self.wallbox = coordinator.wallbox
        self.base_available = True
        self.async_write_ha_state = Mock()

    @property
    def available(self):
        return self.base_available

    @property
    def extra_state_attributes(self):
        return {"base_attribute": "preserved"}


class NumberBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cls = load_class(ROOT / "custom_components/hager_local/number.py", "HagerChargeCurrentLimitNumber", {
            "HagerLocalEntity": BaseEntity, "NumberEntity": type("NumberEntity", (), {}),
            "EntityCategory": SimpleNamespace(CONFIG="config"), "NumberMode": SimpleNamespace(BOX="box"),
            "UnitOfElectricCurrent": SimpleNamespace(AMPERE="A"), "HomeAssistantError": HAError,
            "HagerApiError": ApiError, "CurrentControlError": cc.CurrentControlError,
            "configured_current": cc.configured_current, "current_bounds": cc.current_bounds,
            "current_update_pending": cc.current_update_pending,
            "validate_live_configuration": cc.validate_live_configuration,
        })
        self.wallbox = SimpleNamespace(properties=configuration(), device_id="TEST-WALLBOX")
        self.coordinator = SimpleNamespace(
            wallbox=self.wallbox, async_request_refresh=AsyncMock(),
            api=SimpleNamespace(async_set_max_charge_current=AsyncMock()),
        )
        self.entity = cls(self.coordinator, "test")

    def test_number_has_correct_bounds_and_value(self):
        self.assertTrue(self.entity.available)
        self.assertEqual((self.entity.native_min_value, self.entity.native_max_value, self.entity.native_value), (6, 32, 32))
        self.assertEqual(self.entity._attr_native_step, 1)

    def test_missing_limits_make_entity_unavailable(self):
        del self.wallbox.properties["upperCurrentLimit"]
        self.assertFalse(self.entity.available)
        self.assertIsNone(self.entity.native_value)
        self.assertEqual(self.entity.native_max_value, 6)

    def test_pending_makes_control_unavailable(self):
        self.wallbox.properties["maxChargeCurrentWatingFor"] = True
        self.assertFalse(self.entity.available)

    def test_base_availability_is_respected(self):
        self.entity.base_available = False
        self.assertFalse(self.entity.available)

    def test_attributes_make_cloud_and_beta_explicit(self):
        attributes = self.entity.extra_state_attributes
        self.assertEqual(attributes["base_attribute"], "preserved")
        self.assertEqual(attributes["control_transport"], "Hager Flow cloud")
        self.assertTrue(attributes["experimental"])

    async def test_number_calls_api_and_refresh(self):
        await self.entity.async_set_native_value(16)
        self.coordinator.api.async_set_max_charge_current.assert_awaited_once_with(self.wallbox, 16)
        self.coordinator.async_request_refresh.assert_awaited_once()

    async def test_number_reports_failure_without_optimistic_value(self):
        self.coordinator.api.async_set_max_charge_current.side_effect = ApiError("synthetic failure")
        with self.assertRaises(HAError):
            await self.entity.async_set_native_value(16)
        self.assertEqual(self.entity.native_value, 32)

    async def test_unavailable_number_does_not_send(self):
        self.entity.base_available = False
        with self.assertRaises(HAError):
            await self.entity.async_set_native_value(16)
        self.coordinator.api.async_set_max_charge_current.assert_not_awaited()


SPEC = importlib.util.spec_from_file_location("modbus_probe_test", ROOT / "tools/modbus_probe.py")
mb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mb)


class ModbusTests(unittest.TestCase):
    def test_request_only_uses_read_function(self):
        request = mb.build_read_request(1, 0x1001, 1)
        self.assertEqual(request.hex(), "000100000006010310010001")
        self.assertEqual(request[7], 3)

    def test_invalid_request_is_rejected(self):
        for args in ((1, 0, 0), (1, 0, 17), (1, 65535, 2), (256, 0, 1), (True, 0, 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                mb.build_read_request(*args)

    def test_valid_register_reply(self):
        header = struct.pack(">HHHB", 1, 0, 5, 1)
        result = mb.parse_reply(header, b"\x03\x02\x00\x20", 1, 1)
        self.assertTrue(result["registers_read"])
        self.assertEqual(result["registers"], [32])

    def test_modbus_exception_is_not_a_successful_read(self):
        result = mb.parse_reply(struct.pack(">HHHB", 1, 0, 3, 1), b"\x83\x02", 1, 1)
        self.assertTrue(result["modbus_reply_valid"])
        self.assertFalse(result["registers_read"])

    def test_wrong_transaction_protocol_or_unit(self):
        for fields in ((2, 0, 5, 1), (1, 1, 5, 1), (1, 0, 5, 2)):
            with self.subTest(fields=fields), self.assertRaises(mb.ModbusProbeError):
                mb.parse_reply(struct.pack(">HHHB", *fields), b"\x03\x02\x00\x20", 1, 1)

    def test_malformed_count_is_rejected(self):
        with self.assertRaises(mb.ModbusProbeError):
            mb.parse_reply(struct.pack(">HHHB", 1, 0, 5, 1), b"\x03\x04\x00\x20", 1, 1)

    def test_tcp_connection_is_not_modbus_success(self):
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.recv.side_effect = ConnectionResetError("synthetic reset")
        with patch.object(mb.socket, "create_connection", return_value=stream):
            result = mb.probe("example.invalid")
        self.assertTrue(result["tcp_connected"])
        self.assertFalse(result["modbus_reply_valid"])
        self.assertFalse(result["registers_read"])
        stream.sendall.assert_called_once()
        self.assertEqual(stream.sendall.call_args.args[0][7], 3)


class MetadataTests(unittest.TestCase):
    def test_current_number_translations_exist(self):
        folder = ROOT / "custom_components/hager_local"
        for path in (folder / "strings.json", folder / "translations/en.json", folder / "translations/fr.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data["entity"]["number"]["charge_current_limit"]["name"])

    def test_manifest_is_cloud_and_prerelease(self):
        data = json.loads((ROOT / "custom_components/hager_local/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["iot_class"], "cloud_polling")
        self.assertEqual(data["version"], "0.2.20b1")


if __name__ == "__main__":
    unittest.main()

"""Offline read-only telemetry and privacy regression tests."""
from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "custom_components/hager_local"
SPEC = importlib.util.spec_from_file_location("telemetry_data_test", FOLDER / "telemetry_data.py")
td = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(td)


def parameters():
    return {"sunMode": {"activated": True, "chargingStrategy": "Delayed", "parameterList": [{"minEnergy": "7.5", "daytime": "21600"} for _ in range(7)]}}


class DataTests(unittest.TestCase):
    def test_missing_invalid_and_boolean_numbers_are_unknown(self):
        for value in (None, True, False, "bad", "nan", float("inf"), {}, []):
            with self.subTest(value=value):
                self.assertIsNone(td.finite_number(value))

    def test_numeric_zero_and_strings_survive(self):
        for value, expected in ((0, 0), ("6", 6), (7.5, 7.5), (-10, -10)):
            self.assertEqual(td.finite_number(value), expected)

    def test_bounds_do_not_clamp_fake_values(self):
        for value in (-1, 101, "bad"):
            self.assertIsNone(td.bounded_number(value, 0, 100))

    def test_boolean_is_strict(self):
        for value in ("false", "true", 1, 0, None, {}):
            self.assertIsNone(td.flag(value))
        self.assertIs(td.flag(False), False)
        self.assertIs(td.flag(True), True)

    def test_nested_missing_and_malformed(self):
        for data in ({}, {"deviceState": None}, {"deviceState": "unexpected"}):
            self.assertIsNone(td.nested(data, "deviceState", "deviceConnected"))

    def test_charge_summary_priorities(self):
        data = {"deviceState": {"deviceConnected": False}, "chargingActive": True}
        self.assertEqual(td.charge_summary(data), "offline")
        data["deviceState"] = {"deviceConnected": True, "deviceWorking": False}
        self.assertEqual(td.charge_summary(data), "fault")
        data["deviceState"]["deviceWorking"] = True
        self.assertEqual(td.charge_summary(data), "charging")
        data["chargingActive"] = False
        data["type2Plugged"] = True
        self.assertEqual(td.charge_summary(data), "connected_waiting")
        data["type2Plugged"] = False
        self.assertEqual(td.charge_summary(data), "disconnected")
        self.assertEqual(td.charge_summary({}), "unknown")

    def test_received_plan_keeps_decimal_energy(self):
        rows = td.profile_days(parameters())
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0], {"day": "monday", "energy_kwh": 7.5, "departure": "06:00:00"})
        self.assertEqual(rows[-1]["day"], "sunday")

    def test_midnight_is_not_lost(self):
        data = parameters()
        data["sunMode"]["parameterList"][0]["daytime"] = "0"
        self.assertEqual(td.profile_days(data)[0]["departure"], "00:00:00")

    def test_missing_days_are_not_filled(self):
        for data in ({}, {"sunMode": {}}, {"sunMode": {"parameterList": []}}, {"sunMode": {"parameterList": [{}] * 6}}):
            self.assertEqual(td.profile_days(data), [])

    def test_invalid_values_do_not_become_zero_targets(self):
        data = parameters()
        data["sunMode"]["parameterList"][0] = {"daytime": "86400", "minEnergy": "nan"}
        self.assertEqual(td.profile_days(data)[0], {"day": "monday", "energy_kwh": None, "departure": None})

    def test_profile_mode_unknown_is_not_solar_only(self):
        self.assertEqual(td.profile_summary({}), "unknown")
        self.assertEqual(td.profile_summary({"sunMode": {"activated": True, "chargingStrategy": "new_future_mode"}}), "unknown")
        self.assertEqual(td.profile_summary({"sunMode": {"activated": False}}), "inactive")

    def test_diagnostic_report_drops_identifiers_and_arbitrary_strings(self):
        secret = "PRIVATE_SENTINEL_DO_NOT_EXPORT"
        configuration = {"serial": secret, "ipAddr": secret, "deviceName": secret, "access_token": secret, "keyState": secret, "maxChargeCurrent": 32}
        config = parameters()
        config["account"] = secret
        config["sunMode"]["parameterList"][0]["minEnergy"] = secret
        report = td.public_diagnostic_view(configuration, config)
        text = json.dumps(report, allow_nan=False)
        self.assertNotIn(secret, text)
        self.assertNotIn("access_token", text)
        self.assertNotIn("serial", text)
        self.assertTrue(report["profile_may_be_cached"])

    def test_helpers_do_not_mutate_the_snapshot(self):
        data = parameters()
        original = deepcopy(data)
        td.profile_days(data)
        td.profile_summary(data)
        td.public_diagnostic_view({}, data)
        self.assertEqual(data, original)


class Base:
    def __init__(self, coordinator, key):
        self.coordinator = coordinator
        self.wallbox = coordinator.data.wallboxes[key]

    @property
    def extra_state_attributes(self):
        return {"base_context": "kept"}


def load_telemetry():
    tree = ast.parse((FOLDER / "telemetry.py").read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    namespace = {
        "HagerLocalEntity": Base, "SensorEntity": type("SensorEntity", (), {}),
        "BinarySensorEntity": type("BinarySensorEntity", (), {}),
        "SensorDeviceClass": SimpleNamespace(ENUM="enum", POWER="power", ENERGY="energy"),
        "SensorStateClass": SimpleNamespace(MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing"),
        "EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic"),
        "UnitOfElectricCurrent": SimpleNamespace(AMPERE="A"), "UnitOfEnergy": SimpleNamespace(WATT_HOUR="Wh"),
        "UnitOfPower": SimpleNamespace(WATT="W"), "UnitOfElectricPotential": SimpleNamespace(VOLT="V"), "PERCENTAGE": "%",
    }
    namespace.update({name: getattr(td, name) for name in ("bounded_number", "charge_summary", "finite_number", "flag", "nested", "profile_days", "profile_summary")})
    exec(compile(tree, "telemetry.py", "exec"), namespace)
    return namespace


class EntityTests(unittest.TestCase):
    def setUp(self):
        self.module = load_telemetry()
        self.wallbox = SimpleNamespace(device_id="SYNTHETIC", properties={"numberPhases": 1, "pmPowerL1": 2300}, evse_parameters=parameters())
        self.coordinator = SimpleNamespace(data=SimpleNamespace(wallboxes={"test": self.wallbox}))
        self.sensors = self.module["telemetry_sensors"](self.coordinator, "test")
        self.binary = self.module["telemetry_binary_sensors"](self.coordinator, "test")

    def test_factory_has_22_distinct_entities(self):
        entities = self.sensors + self.binary
        self.assertEqual(len(entities), 22)
        self.assertEqual(len({e._attr_unique_id for e in entities}), 22)

    def test_inactive_phases_default_disabled_but_remain_available_to_enable(self):
        sensors = {e._attr_translation_key: e for e in self.sensors}
        self.assertTrue(sensors["phase_l1_power"]._attr_entity_registry_enabled_default)
        self.assertFalse(sensors["phase_l2_power"]._attr_entity_registry_enabled_default)
        self.assertFalse(sensors["phase_l3_energy"]._attr_entity_registry_enabled_default)
        self.assertEqual(sensors["phase_l1_power"].native_value, 2300)
        self.assertIsNone(sensors["phase_l2_power"].native_value)

    def test_three_phase_installation_enables_all_phase_entities(self):
        self.wallbox.properties["numberPhases"] = 3
        entities = self.module["telemetry_sensors"](self.coordinator, "test")
        phase_entities = [e for e in entities if e._attr_translation_key.startswith("phase_")]
        self.assertTrue(all(e._attr_entity_registry_enabled_default for e in phase_entities))

    def test_unknown_flag_not_reported_false(self):
        for entity in self.binary:
            self.assertIsNone(entity.is_on)

    def test_profile_cache_warning_always_present(self):
        profile = next(e for e in self.sensors if e._attr_translation_key == "profile_received")
        self.assertTrue(profile.extra_state_attributes["profile_may_be_cached"])
        self.assertEqual(profile.extra_state_attributes["base_context"], "kept")

    def test_every_entity_has_all_translations(self):
        for path in (FOLDER / "strings.json", FOLDER / "translations/en.json", FOLDER / "translations/fr.json"):
            translations = json.loads(path.read_text(encoding="utf-8"))["entity"]
            for domain, entities in (("sensor", self.sensors), ("binary_sensor", self.binary)):
                for entity in entities:
                    self.assertTrue(translations[domain][entity._attr_translation_key]["name"])
                    options = getattr(entity, "_attr_options", None)
                    if options:
                        self.assertEqual(set(options), set(translations[domain][entity._attr_translation_key]["state"]))

    def test_new_modules_contain_no_charger_setters_or_network_calls(self):
        for name in ("telemetry.py", "telemetry_data.py", "diagnostics.py"):
            tree = ast.parse((FOLDER / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertFalse(node.name.startswith(("async_set_", "async_turn_", "async_select_")))
                if isinstance(node, ast.Call):
                    function = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    self.assertNotIn(function, {"_request_json", "ws_connect", "sendall", "async_set_max_charge_current"})


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostics_when_integration_is_unloaded(self):
        tree = ast.parse((FOLDER / "diagnostics.py").read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        tree.body.insert(0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
        namespace = {"finite_number": td.finite_number, "public_diagnostic_view": td.public_diagnostic_view}
        exec(compile(ast.fix_missing_locations(tree), "diagnostics.py", "exec"), namespace)
        entry = SimpleNamespace(runtime_data=None, options={})
        data = await namespace["async_get_config_entry_diagnostics"](None, entry)
        self.assertFalse(data["snapshot_available"])
        self.assertEqual(data["wallboxes"], [])
        self.assertEqual(data["integration_version"], "0.2.20b2")


if __name__ == "__main__":
    unittest.main()

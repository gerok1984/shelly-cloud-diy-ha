"""Tests for BLU TRV aliases resolved from the cloud account device list."""
from __future__ import annotations

from typing import Any

from custom_components.shelly_cloud_diy.api.cloud_control import (
    _VIRTUAL_COMPONENT_KEY_RE as API_CONFIG_KEY_RE,
)
from custom_components.shelly_cloud_diy.climate import ShellyBluTrvClimate
from custom_components.shelly_cloud_diy.coordinator import (
    _VIRTUAL_COMPONENT_KEY_RE as COORD_CONFIG_KEY_RE,
)

DEVICE_ID = "gateway01"


class _FakeCoordinator:
    def __init__(
        self,
        status: dict[str, Any],
        blu_trv_names: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.devices = {
            DEVICE_ID: {
                "status": status,
                "device_code": "S3GW-1DBT001",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True
        self.virtual_configs = {}
        self.blu_trv_names = blu_trv_names or {}


_STATUS = {
    "blutrv:200": {
        "id": 200,
        "target_C": 10,
        "current_C": 24.3,
        "paired": True,
    },
    "blutrv:201": {
        "id": 201,
        "target_C": 10,
        "current_C": 24.4,
        "paired": True,
    },
}


def test_blutrv_no_longer_uses_v2_settings_probe() -> None:
    for key in ("blutrv:200", "bthomedevice:200"):
        assert not COORD_CONFIG_KEY_RE.match(key)
        assert not API_CONFIG_KEY_RE.match(key)


def test_climate_uses_account_list_alias_cache() -> None:
    coordinator = _FakeCoordinator(
        _STATUS,
        {
            DEVICE_ID: {
                "blutrv:200": "Salón 1",
                "blutrv:201": "Salón 2",
            }
        },
    )
    first = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:200", display_index=1
    )
    second = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:201", display_index=2
    )
    assert first.name == "Salón 1"
    assert second.name == "Salón 2"


def test_climate_falls_back_without_account_alias() -> None:
    coordinator = _FakeCoordinator(_STATUS)
    first = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:200", display_index=1
    )
    second = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:201", display_index=2
    )
    assert first.name == "BLU TRV"
    assert second.name == "BLU TRV 2"

"""Tests for BLU TRV names resolved through linked BTHome config."""
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
        virtual_configs: dict[str, dict[str, dict[str, Any]]],
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
        self.virtual_configs = virtual_configs


_STATUS = {
    "sys": {"mac": "AABBCC"},
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
    "bthomedevice:200": {"id": 200},
    "bthomedevice:201": {"id": 201},
    "bthomedevice:202": {"id": 202},
}


def test_probe_keys_trigger_and_survive_v2_config_filter() -> None:
    for key in (
        "blutrv:200",
        "blutrv:201",
        "bthomedevice:200",
        "bthomedevice:201",
        "bthomedevice:202",
    ):
        assert COORD_CONFIG_KEY_RE.match(key)
        assert API_CONFIG_KEY_RE.match(key)


def test_climate_follows_blutrv_trv_pointer_to_bthome_name() -> None:
    coordinator = _FakeCoordinator(
        _STATUS,
        {
            DEVICE_ID: {
                "blutrv:200": {
                    "id": 200,
                    "name": None,
                    "trv": "bthomedevice:200",
                },
                "blutrv:201": {
                    "id": 201,
                    "name": None,
                    "trv": "bthomedevice:201",
                },
                "bthomedevice:200": {"id": 200, "name": "Salón 1"},
                "bthomedevice:201": {"id": 201, "name": "Salón 2"},
                "bthomedevice:202": {"id": 202, "name": "H&T Fueros"},
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


def test_climate_falls_back_when_linked_bthome_has_no_name() -> None:
    coordinator = _FakeCoordinator(
        _STATUS,
        {
            DEVICE_ID: {
                "blutrv:200": {
                    "id": 200,
                    "name": None,
                    "trv": "bthomedevice:200",
                },
                "bthomedevice:200": {"id": 200, "name": None},
            }
        },
    )
    valve = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:200", display_index=1
    )
    assert valve.name == "BLU TRV"

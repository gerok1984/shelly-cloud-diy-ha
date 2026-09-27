"""Tests for Shelly BLU TRV names exposed by BLU Gateway Gen3."""
from __future__ import annotations

from typing import Any

from custom_components.shelly_cloud_diy.climate import (
    ShellyBluTrvClimate,
    _measurement_name,
)

DEVICE_ID = "gateway01"


class _FakeCoordinator:
    def __init__(self, status: dict[str, Any]) -> None:
        self.devices = {
            DEVICE_ID: {
                "status": status,
                "device_code": "S3GW-1DBT001",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True


_STATUS = {
    "blutrv:200": {
        "id": 200,
        "target_C": 10,
        "current_C": 24.3,
        "pos": 0,
        "connected": True,
        "paired": True,
    },
    "blutrv:201": {
        "id": 201,
        "target_C": 10,
        "current_C": 24.4,
        "pos": 0,
        "connected": True,
        "paired": True,
    },
    "_measurements": [
        {
            "name": "Salón 1",
            "value": "blutrv_rstatus:200",
            "type": "trv",
        },
        {
            "name": "Salón 2",
            "value": "blutrv_rstatus:201",
            "type": "trv",
        },
    ],
}


def test_measurement_names_map_remote_status_id_to_live_component() -> None:
    assert _measurement_name(_STATUS, "blutrv:200") == "Salón 1"
    assert _measurement_name(_STATUS, "blutrv:201") == "Salón 2"


def test_climate_entities_use_shelly_app_names() -> None:
    coordinator = _FakeCoordinator(_STATUS)

    salon_1 = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:200", display_index=1
    )
    salon_2 = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:201", display_index=2
    )

    assert salon_1.name == "Salón 1"
    assert salon_2.name == "Salón 2"


def test_name_falls_back_when_measurements_are_missing() -> None:
    coordinator = _FakeCoordinator(
        {
            "blutrv:200": {
                "id": 200,
                "target_C": 10,
                "current_C": 24.3,
                "paired": True,
            }
        }
    )
    valve = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:200", display_index=1
    )
    assert valve.name == "BLU TRV"


def test_measurement_name_ignores_other_types_and_blank_names() -> None:
    status = {
        "_measurements": [
            {
                "name": "Wrong",
                "value": "blutrv_rstatus:200",
                "type": "temperature",
            },
            {"name": "   ", "value": "blutrv_rstatus:200", "type": "trv"},
        ]
    }
    assert _measurement_name(status, "blutrv:200") is None


def test_direct_component_reference_is_supported() -> None:
    status = {
        "_measurements": [
            {"name": "Salón 1", "value": "blutrv:200", "type": "trv"},
        ]
    }
    assert _measurement_name(status, "blutrv:200") == "Salón 1"

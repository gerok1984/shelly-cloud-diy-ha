"""Tests for BLU TRV names resolved from Shelly account aliases."""
from __future__ import annotations

from typing import Any

from custom_components.shelly_cloud_diy.climate import (
    ShellyBluTrvClimate,
    _blu_trv_account_alias,
    _normalised_device_suffix,
)

DEVICE_ID = "gateway01"


class _FakeCoordinator:
    def __init__(
        self,
        status: dict[str, Any],
        aliases: dict[str, str] | None = None,
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
        self.account_device_names = aliases or {}
        self.virtual_configs = {}


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
    "blutrv_rinfo:200": {
        "device_info": {"id": "shellyblutrv-f8447728bf0a"}
    },
    "blutrv_rinfo:201": {
        "device_info": {"id": "shellyblutrv-f8447728e0e6"}
    },
}


def test_normalises_prefixed_and_plain_child_ids() -> None:
    assert _normalised_device_suffix("shellyblutrv-f8447728bf0a") == "f8447728bf0a"
    assert _normalised_device_suffix("f8:44:77:28:bf:0a") == "f8447728bf0a"
    assert _normalised_device_suffix("f8447728bf0a") == "f8447728bf0a"


def test_resolves_names_from_plain_mac_account_ids() -> None:
    aliases = {
        "f8447728bf0a": "Salón 1",
        "f8447728e0e6": "Salón 2",
    }
    assert _blu_trv_account_alias(_STATUS, "blutrv:200", aliases) == "Salón 1"
    assert _blu_trv_account_alias(_STATUS, "blutrv:201", aliases) == "Salón 2"


def test_resolves_names_from_prefixed_account_ids() -> None:
    aliases = {
        "shellyblutrv-f8447728bf0a": "Salón 1",
        "shellyblutrv-f8447728e0e6": "Salón 2",
    }
    assert _blu_trv_account_alias(_STATUS, "blutrv:200", aliases) == "Salón 1"
    assert _blu_trv_account_alias(_STATUS, "blutrv:201", aliases) == "Salón 2"


def test_climate_uses_account_alias() -> None:
    coordinator = _FakeCoordinator(
        _STATUS,
        {
            "f8447728bf0a": "Salón 1",
            "f8447728e0e6": "Salón 2",
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


def test_climate_falls_back_without_child_alias() -> None:
    coordinator = _FakeCoordinator(_STATUS, {"gateway01": "TRV Fueros"})
    valve = ShellyBluTrvClimate(
        coordinator, DEVICE_ID, "blutrv:200", display_index=1
    )
    assert valve.name == "BLU TRV"

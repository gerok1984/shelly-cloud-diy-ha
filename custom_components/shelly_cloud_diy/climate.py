"""Climate platform for Shelly BLU TRV devices exposed by a BLU Gateway Gen3."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_DEVICE_REMOVED
from .coordinator import ShellyCloudCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity

_BLUTRV_KEY_RE = re.compile(r"^blutrv:(\d+)$")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly BLU TRV climate entities."""
    coordinator: ShellyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_entities: set[str] = set()

    def create_climates(device_id: str) -> list[ClimateEntity]:
        entities: list[ClimateEntity] = []
        if not coordinator.is_enabled(device_id):
            return entities

        status = coordinator.devices.get(device_id, {}).get("status", {})
        if not isinstance(status, dict):
            return entities

        keys = sorted(
            [
                key
                for key, value in status.items()
                if _BLUTRV_KEY_RE.match(key) and isinstance(value, dict)
            ],
            key=lambda key: int(key.split(":", 1)[1]),
        )

        for display_index, key in enumerate(keys, start=1):
            unique_id = f"{device_id}_{key}_climate"
            if unique_id in created_entities:
                continue
            created_entities.add(unique_id)
            entities.append(
                ShellyBluTrvClimate(
                    coordinator,
                    device_id,
                    key,
                    display_index=display_index,
                )
            )
        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        entities = create_climates(device_id)
        if entities:
            async_add_entities(entities)

    @callback
    def async_forget_device(device_id: str) -> None:
        for unique_id in [
            key for key in created_entities if key.startswith(device_id)
        ]:
            created_entities.discard(unique_id)

    entities: list[ClimateEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_climates(device_id))
    if entities:
        async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_DEVICE_REMOVED, async_forget_device)
    )


class ShellyBluTrvClimate(ShellyBaseEntity, ClimateEntity):
    """Shelly BLU TRV represented through its BLU Gateway Gen3."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_min_temp = 4.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        component_key: str,
        *,
        display_index: int,
    ) -> None:
        super().__init__(coordinator, device_id, 0)
        self._component_key = component_key
        self._display_index = display_index
        self._attr_unique_id = f"{device_id}_{component_key}_climate"

    @property
    def name(self) -> str:
        """Return the configured component name, or a stable fallback."""
        configured = self.virtual_component_name(self._component_key)
        if configured:
            return configured
        return "BLU TRV" if self._display_index == 1 else f"BLU TRV {self._display_index}"

    def _component(self) -> dict[str, Any]:
        value = self.device_status.get(self._component_key)
        return value if isinstance(value, dict) else {}

    @property
    def available(self) -> bool:
        component = self._component()
        return (
            bool(component)
            and component.get("paired") is not False
            and super().available
        )

    @property
    def supported_features(self) -> ClimateEntityFeature:
        if (
            self.coordinator.cloud_control_connected
            and self.coordinator.is_cloud_controllable(self._device_id)
        ):
            return ClimateEntityFeature.TARGET_TEMPERATURE
        return ClimateEntityFeature(0)

    @property
    def current_temperature(self) -> float | None:
        value = self._component().get("current_C")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def target_temperature(self) -> float | None:
        value = self._component().get("target_C")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def hvac_action(self) -> HVACAction:
        pos = self._component().get("pos")
        if isinstance(pos, (int, float)) and pos > 0:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        component = self._component()
        attrs: dict[str, Any] = {}

        for source, target in (
            ("pos", "valve_position"),
            ("battery", "battery"),
            ("rssi", "rssi"),
            ("last_updated_ts", "last_updated_ts"),
            ("rsv", "remote_state_revision"),
        ):
            if isinstance(component.get(source), (int, float)):
                attrs[target] = component[source]

        if isinstance(component.get("errors"), list):
            attrs["errors"] = component["errors"]
        if "rpc" in component:
            attrs["rpc_capable"] = bool(component["rpc"])
        if "paired" in component:
            attrs["paired"] = bool(component["paired"])

        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if not isinstance(temperature, (int, float)):
            return

        await self.coordinator.async_set_blutrv_target(
            self._device_id,
            self._component_key,
            float(temperature),
        )

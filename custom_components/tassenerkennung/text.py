"""Name for a user-defined vessel profile."""

from homeassistant.components.text import TextEntity, TextMode

from .entity import CupEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([NewVesselName(entry.runtime_data)])


class NewVesselName(CupEntity, TextEntity):
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 60
    _attr_pattern = r"[^<>]{0,60}"

    def __init__(self, coordinator):
        super().__init__(coordinator, "learning_name", "Name für neuen Becher", "mdi:form-textbox")

    @property
    def native_value(self):
        return self.coordinator.learning.new_name

    @property
    def available(self):
        return bool(super().available and not self.coordinator.learning.active)

    async def async_set_value(self, value):
        self.coordinator.learning.new_name = value.strip()[:60]
        self.async_write_ha_state()

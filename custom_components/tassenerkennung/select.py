"""Target profile for the guided vessel learner."""

from homeassistant.components.select import SelectEntity

from .entity import CupEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([LearningProfile(entry.runtime_data),SourceCamera(entry.runtime_data)])


class LearningProfile(CupEntity, SelectEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "learning_profile", "Einlernprofil", "mdi:cup-outline")

    @property
    def options(self):
        return self.coordinator.learning.options

    @property
    def current_option(self):
        return self.coordinator.learning.selected

    @property
    def available(self):
        return bool(super().available and not self.coordinator.learning.active)

    async def async_select_option(self, option):
        if option not in self.options:
            raise ValueError("Ungültiges Einlernprofil")
        self.coordinator.learning.selected = option
        self.async_write_ha_state()


class SourceCamera(CupEntity, SelectEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "source_camera", "Quellkamera für Wechsel", "mdi:camera-switch")

    @property
    def options(self):
        own={state.entity_id for state in self.hass.states.async_all('camera')
             if state.entity_id.startswith('camera.tassenerkennung_')}
        return sorted(state.entity_id for state in self.hass.states.async_all('camera')
                      if state.entity_id not in own and state.state not in ('unavailable','unknown'))

    @property
    def current_option(self):
        return self.coordinator.source_camera_candidate

    @property
    def available(self):
        return bool(super().available and not self.coordinator.learning.active and self.options)

    async def async_select_option(self, option):
        if option not in self.options:
            raise ValueError("Ungültige Quellkamera")
        self.coordinator.source_camera_candidate=option
        self.async_write_ha_state()


"""Explicit image refresh and position confirmation. No coffee actions."""

import asyncio

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .entity import CupEntity


async def learning_action(operation):
    try:return await operation
    except ValueError as error:raise HomeAssistantError(str(error)) from error


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CheckNow(entry.runtime_data), ConfirmPosition(entry.runtime_data),
                        StartLearning(entry.runtime_data), CaptureLearning(entry.runtime_data),
                        SaveLearning(entry.runtime_data), CancelLearning(entry.runtime_data),
                        ApplyCamera(entry.runtime_data)])


class CheckNow(CupEntity, ButtonEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "check", "Jetzt prüfen", "mdi:refresh")

    async def async_press(self):
        lights=["switch.kueche_schrankbeleuchtung_links","switch.kueche_schrankbeleuchtung_rechts"]
        if not all(self.hass.states.is_state(e,"on") for e in lights):
            await self.hass.services.async_call("switch","turn_on",{"entity_id":lights},blocking=True)
            await asyncio.sleep(3)
        await self.coordinator.async_request_refresh()


class ConfirmPosition(CupEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator, "position", "Ausrichtung bestätigen", "mdi:crosshairs-gps")

    async def async_press(self):
        lights=["switch.kueche_schrankbeleuchtung_links","switch.kueche_schrankbeleuchtung_rechts"]
        if not all(self.hass.states.is_state(e,"on") for e in lights):
            await self.hass.services.async_call("switch","turn_on",{"entity_id":lights},blocking=True)
            await asyncio.sleep(3)
        await self.coordinator.async_confirm_position()


class StartLearning(CupEntity, ButtonEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "learning_start", "Einlernen starten", "mdi:school")

    @property
    def available(self):
        return bool(super().available and not self.coordinator.learning.active)

    async def async_press(self):
        await learning_action(self.coordinator.learning.start())


class CaptureLearning(CupEntity, ButtonEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "learning_capture", "Aufnahme bestätigen", "mdi:camera-plus")

    @property
    def available(self):
        return bool(super().available and self.coordinator.learning.active
                    and self.coordinator.learning.stage in {"empty", "front", "rotated", "offset"})

    async def async_press(self):
        await learning_action(self.coordinator.learning.capture())


class SaveLearning(CupEntity, ButtonEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "learning_save", "Profil speichern", "mdi:content-save-check")

    @property
    def available(self):
        learning = self.coordinator.learning
        return bool(super().available and learning.active and learning.stage == "review"
                    and not learning.session.error)

    async def async_press(self):
        await learning_action(self.coordinator.learning.save())


class CancelLearning(CupEntity, ButtonEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "learning_cancel", "Einlernen abbrechen", "mdi:cancel")

    @property
    def available(self):
        return bool(super().available and self.coordinator.learning.active)

    async def async_press(self):
        await learning_action(self.coordinator.learning.cancel())


class ApplyCamera(CupEntity,ButtonEntity):
    def __init__(self,coordinator):
        super().__init__(coordinator,"apply_camera","Ausgewählte Kamera übernehmen","mdi:camera-check")

    @property
    def available(self):
        candidate=self.coordinator.source_camera_candidate
        return bool(super().available and not self.coordinator.learning.active and candidate
                    and candidate!=self.coordinator.settings.camera and self.hass.states.get(candidate))

    async def async_press(self):
        candidate=self.coordinator.source_camera_candidate
        if not candidate or candidate==self.coordinator.settings.camera or not self.hass.states.get(candidate):
            raise ValueError("Bitte zuerst eine andere verfügbare Kamera auswählen")
        options={**self.coordinator.entry.options,"camera":candidate}
        self.hass.config_entries.async_update_entry(self.coordinator.entry,options=options)

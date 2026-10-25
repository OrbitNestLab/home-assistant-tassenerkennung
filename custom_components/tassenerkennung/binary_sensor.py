"""A current, position-checked cup observation. Not a brewing authorization."""

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import CupEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CupPresent(entry.runtime_data), LearningMode(entry.runtime_data)])


class CupPresent(CupEntity, BinarySensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "cup", "Tasse unter dem Auslauf", "mdi:coffee")

    @property
    def available(self):
        observation = self.coordinator.data.get("observation")
        return bool(super().available and not self.coordinator.learning.active and self.coordinator.fresh
                    and observation is not None and observation.present is not None)

    @property
    def is_on(self):
        observation = self.coordinator.data.get("observation")
        return bool(self.coordinator.fresh and observation and observation.present is True)

    @property
    def extra_state_attributes(self):
        observation = self.coordinator.data.get("observation")
        return {"aktuell_bestaetigt": bool(self.coordinator.fresh and observation and observation.current_confirmed),
                "anzeige_gehalten": bool(observation and observation.held),
                "neu_eingestellt_am": self.coordinator.placed_at,
                "kamera": self.coordinator.settings.camera,
                "bildzeitpunkt": self.coordinator.data.get("captured_at"),
                "bestaetigungen": observation.confirmations if observation else 0,
                "erforderliche_bestaetigungen": self.coordinator.settings.confirmations,
                "mindestdauer_sekunden": self.coordinator.settings.confirm_seconds,
                "anzeigehaltezeit_sekunden": self.coordinator.settings.hold_seconds}


class LearningMode(CupEntity, BinarySensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "learning_mode", "Einlernmodus", "mdi:school-outline")

    @property
    def is_on(self):
        return self.coordinator.learning.active

    @property
    def extra_state_attributes(self):
        learning = self.coordinator.learning
        return {"schritt": learning.stage, "status": learning.status,
                "profil": learning.session.name if learning.session else None,
                "getraenkeaktionen_gesperrt": learning.active}

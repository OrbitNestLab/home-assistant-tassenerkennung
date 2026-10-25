"""Compact status and diagnostics."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .entity import CupEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CupStatus(entry.runtime_data), CupConfidence(entry.runtime_data), CupType(entry.runtime_data),
                        LearningStatus(entry.runtime_data)])


class CupStatus(CupEntity, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "status", "Status", "mdi:coffee-outline")

    @property
    def native_value(self):
        if self.coordinator.learning.active:
            return "Einlernmodus · " + self.coordinator.learning.status
        return self.coordinator.data.get("status", "Warte auf Kamerabild")

    @property
    def extra_state_attributes(self):
        observation = self.coordinator.data.get("observation")
        return {"version": "0.6.0", "bildzeitpunkt": self.coordinator.data.get("captured_at"),
                "bild_aktuell": self.coordinator.fresh, "referenz_vorhanden": self.coordinator.reference is not None,
                "positionsaehnlichkeit": round(observation.alignment.similarity, 3) if observation else None,
                "verschiebung_in_pruefpixeln": list(observation.alignment.shift) if observation else None,
                "auswertungszeit_ms": round(observation.inference_ms, 1) if observation else None,
                "pruefabstand_sekunden": self.coordinator.settings.interval,
                "aufnahmezeit_ms": round(self.coordinator.data.get("capture_ms",0),1),
                "gesamtzeit_ms": round(self.coordinator.data.get("total_ms",0),1),
                "kamera_korrigiert": bool(self.coordinator.reference and self.coordinator.reference.get("camera_transform")),
                "aktuell_bestaetigt": bool(self.coordinator.fresh and observation and observation.current_confirmed),
                "anzeige_gehalten": bool(observation and observation.held),
                "nachfuehrung": self.coordinator.recovery_state,
                "nachfuehrung_zeitpunkt": self.coordinator.recovery_last,
                "tassenplatz_leer_seit": self.coordinator.absent_since,
                "bechertyp": observation.vessel_type if observation and self.coordinator.fresh else "unbekannt",
                "becherprofile": {"Babybecher": "Eingelernt · warme Milch nur auswählen",
                                  "Latte-Glas": "Wartet auf Beispielbilder · Latte nur auswählen",
                                  "Espresso-Glas": "Gesperrt bis eingelernt · dann Espresso starten"}}


class CupConfidence(CupEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator):
        super().__init__(coordinator, "confidence", "Erkennungswert", "mdi:chart-bell-curve")

    @property
    def available(self):
        return bool(super().available and not self.coordinator.learning.active and self.coordinator.fresh
                    and self.coordinator.data.get("observation"))

    @property
    def native_value(self):
        observation = self.coordinator.data.get("observation")
        return round(observation.confidence*100, 1) if observation else None


class CupType(CupEntity, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "vessel_type", "Bechertyp", "mdi:cup")

    @property
    def available(self):
        return bool(super().available and not self.coordinator.learning.active and self.coordinator.fresh
                    and self.coordinator.data.get("observation"))

    @property
    def native_value(self):
        observation = self.coordinator.data.get("observation")
        return observation.vessel_type if observation and self.coordinator.fresh else "unbekannt"

    @property
    def extra_state_attributes(self):
        observation = self.coordinator.data.get("observation")
        return {"bildzeitpunkt": self.coordinator.data.get("captured_at"),
                "vergleichswert": round(observation.vessel_similarity*100, 1) if observation else None,
                "platzierung": observation.vessel_episode if observation and self.coordinator.fresh else None,
                "bestaetigungsdauer_sekunden": self.coordinator.engine.profiles.duration_for(observation.vessel_type) if observation and self.coordinator.engine.profiles else None,
                "aktuell_bestaetigt": bool(observation and self.coordinator.fresh and observation.vessel_current_confirmed),
                "anzeige_gehalten": bool(observation and observation.vessel_held),
                "getraenkestart": False,
                "verfahren": "Gelernter Bildvergleich; kein Wahrscheinlichkeitswert"}


class LearningStatus(CupEntity, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "learning_status", "Einlernstatus", "mdi:progress-pencil")

    @property
    def native_value(self):
        return self.coordinator.learning.status

    @property
    def extra_state_attributes(self):
        learning = self.coordinator.learning
        return {"schritt": learning.stage,
                "profil": learning.session.name if learning.session else learning.selected,
                "aufnahmen": len(learning.session.samples) if learning.session else 0,
                "benoetigte_aufnahmen": 9,
                "hinweis": "Neue Profile haben keine Geräteaktion; diese wird separat eingerichtet."}

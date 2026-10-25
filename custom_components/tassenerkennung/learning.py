"""Guided, local vessel learning without any beverage actions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import unicodedata

import numpy as np

from .vision import covers_zone, open_image
from .tracking import shifted_settings


NEW_PROFILE = "+ Neuer Becher"
BUILTIN_NAMES = {
    "kaffeetasse": "Kaffeetasse",
    "babybecher": "Babybecher",
    "latteglas": "Latteglas",
    "espressoglas": "Espressoglas",
}
STAGE_LABELS = {
    "idle": "Bereit",
    "empty": "Abstellfläche leeren und Aufnahme drücken",
    "front": "Becher mittig aufstellen und Aufnahme drücken",
    "rotated": "Becher etwa eine Vierteldrehung drehen und Aufnahme drücken",
    "offset": "Becher leicht versetzt unter den Auslauf stellen und Aufnahme drücken",
    "review": "Aufnahmen geprüft · Profil kann gespeichert werden",
    "error": "Aufnahme fehlgeschlagen",
}


def safe_profile_id(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if not normalized:
        raise ValueError("Bitte einen eindeutigen Namen eingeben")
    return "benutzer_" + normalized[:40]


@dataclass
class LearningSession:
    profile_id: str
    name: str
    stage: str = "empty"
    samples: list = field(default_factory=list)
    empty_samples: list = field(default_factory=list)
    source_size: list | None = None
    previous_lights: dict = field(default_factory=dict)
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LearningManager:
    """A deliberate four-step learner. It never calls coffee-machine services."""

    def __init__(self, coordinator, profile_path: Path, backup_store):
        self.coordinator = coordinator
        self.profile_path = profile_path
        self.backup_store = backup_store
        self.selected = "Kaffeetasse"
        self.new_name = ""
        self.session: LearningSession | None = None

    @property
    def active(self):
        return self.session is not None

    @property
    def stage(self):
        return self.session.stage if self.session else "idle"

    @property
    def status(self):
        if not self.session:
            return STAGE_LABELS["idle"]
        if self.session.error:
            return self.session.error
        return STAGE_LABELS[self.session.stage]

    @property
    def options(self):
        profiles = self.coordinator.engine.profiles
        names = dict(BUILTIN_NAMES)
        if profiles:
            names.update(profiles.names)
        ordered = [names[key] for key in BUILTIN_NAMES]
        custom = sorted(value for key, value in names.items() if key not in BUILTIN_NAMES and key != "leer")
        return ordered + custom + [NEW_PROFILE]

    def _selection(self):
        profiles = self.coordinator.engine.profiles
        names = dict(BUILTIN_NAMES)
        if profiles:
            names.update(profiles.names)
        if self.selected == NEW_PROFILE:
            clean = self.new_name.strip()
            if len(clean) < 2:
                raise ValueError("Bitte zuerst einen Namen für den neuen Becher eingeben")
            profile_id = safe_profile_id(clean)
            if profile_id in names:
                raise ValueError("Dieser Bechername ist bereits vorhanden")
            return profile_id, clean[:60]
        for profile_id, name in names.items():
            if name == self.selected:
                return profile_id, name
        raise ValueError("Bitte ein gültiges Becherprofil wählen")

    def _notify(self):
        self.coordinator.async_update_listeners()

    async def start(self):
        if self.active:
            raise ValueError("Ein Einlernvorgang läuft bereits")
        profile_id, name = self._selection()
        previous = {entity: self.coordinator.hass.states.get(entity).state
                    if self.coordinator.hass.states.get(entity) else "unknown"
                    for entity in self.coordinator.lights}
        self.session = LearningSession(profile_id, name, previous_lights=previous)
        self.coordinator.engine.confirmation.reset()
        self.coordinator.engine.last_positive = None
        if self.coordinator.engine.profiles:
            self.coordinator.engine.profiles.invalidate()
        try:
            await self.coordinator.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self.coordinator.lights}, blocking=True)
            await asyncio.sleep(3)
            await self.coordinator.async_request_refresh()
        except Exception as error:
            self.session.error = "Arbeitsflächenlicht oder Kamerabild nicht verfügbar"
            self._notify()
            raise ValueError(self.session.error) from error
        self._notify()

    async def cancel(self):
        session = self.session
        self.session = None
        self.coordinator.engine.confirmation.reset()
        self.coordinator.engine.last_positive = None
        if self.coordinator.engine.profiles:
            self.coordinator.engine.profiles.invalidate()
        if session:
            turn_off = [entity for entity, state in session.previous_lights.items() if state == "off"]
            if turn_off:
                await self.coordinator.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": turn_off}, blocking=True)
        await self.coordinator.async_request_refresh()
        self._notify()

    async def _collect(self, expect_empty: bool):
        observations, samples, seen = [], [], set()
        active = (shifted_settings(self.coordinator.settings, self.coordinator.reference)
                  if self.coordinator.reference and self.coordinator.reference.get("landmarks")
                  else self.coordinator.settings)
        for _ in range(12):
            await self.coordinator.async_request_refresh()
            data = self.coordinator.data
            observation = data.get("observation")
            stamp = str(data.get("captured_at"))
            if not observation or not self.coordinator.fresh or stamp in seen:
                await asyncio.sleep(.45)
                continue
            seen.add(stamp)
            if not observation.alignment.ok:
                raise ValueError("Kameraausrichtung ist nicht sicher")
            cups = [item for item in observation.detections
                    if item.class_id == 41 and item.in_zone(active.zone, observation.image_size)]
            zone = active.zone.pixels(observation.image_size)
            blocked = any(item.class_id == 0 and covers_zone(item.box, zone) > .1
                          for item in observation.detections)
            if blocked:
                raise ValueError("Der Prüfbereich ist verdeckt")
            if expect_empty and cups:
                raise ValueError("Für die Gegenprobe muss die Abstellfläche leer sein")
            if not expect_empty and len(cups) != 1:
                raise ValueError("Genau ein Becher muss mittig unter dem Auslauf stehen")
            observations.append(observation)
            image = open_image(self.coordinator.latest_content)
            samples.append(self.coordinator.engine.profiles.extract_sample(image, active))
            if self.session and self.session.source_size is None:
                self.session.source_size=list(image.size)
            if len(observations) >= 3:
                return samples
            await asyncio.sleep(.7)
        raise ValueError("Die Kamera hat nicht genügend neue Bilder geliefert")

    async def capture(self):
        if not self.session:
            raise ValueError("Bitte zuerst den Einlernmodus starten")
        if self.session.stage not in {"empty", "front", "rotated", "offset"}:
            raise ValueError("In diesem Schritt ist keine Aufnahme vorgesehen")
        self.session.error = None
        try:
            samples = await self._collect(self.session.stage == "empty")
            if self.session.stage == "empty":
                self.session.empty_samples=samples
                self.session.stage = "front"
            else:
                self.session.samples.extend(samples)
                self.session.stage = {"front": "rotated", "rotated": "offset", "offset": "review"}[self.session.stage]
                if self.session.stage == "review":
                    self._validate_candidate()
        except ValueError as error:
            self.session.error = str(error)
            self._notify()
            raise
        self._notify()

    def _validate_candidate(self):
        session = self.session
        if not session or len(session.samples) < 9:
            raise ValueError("Es fehlen unterschiedliche Aufnahmen des Bechers")
        arrays = [np.asarray(sample, dtype=np.float32) / 255 for sample in session.samples]
        profiles = self.coordinator.engine.profiles
        # Each view must be sufficiently informative. Similarity to a different known
        # profile is rejected unless the user deliberately extends that same profile.
        if any(float(array.mean()) < .045 or float(array.std()) < .025 for array in arrays):
            raise ValueError("Der Becher ist im Bild zu dunkel oder zu kontrastarm")
        for profile_id, known in profiles.samples.items():
            if profile_id in {session.profile_id, "leer"}:
                continue
            best = max((profiles.similarity(candidate, reference)[0]
                        for candidate in arrays for reference in known), default=0)
            if best >= .985:
                raise ValueError(f"Der Becher ist dem Profil „{profiles.display_name(profile_id)}“ zu ähnlich")

    async def save(self):
        if not self.session or self.session.stage != "review" or self.session.error:
            raise ValueError("Das Profil ist noch nicht fertig geprüft")
        self._validate_candidate()
        profiles = self.coordinator.engine.profiles
        previous = profiles.export()
        await self.backup_store.async_save(previous)
        if profiles.data.get('camera') != self.coordinator.settings.camera or profiles.data.get('source_size') != self.session.source_size:
            profiles.reset_for_camera(self.coordinator.settings.camera,self.session.source_size,self.session.empty_samples)
        profiles.add_samples(self.session.profile_id, self.session.name, self.session.samples)
        payload = profiles.export()

        def atomic_write():
            temporary = self.profile_path.with_suffix(".learning.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.profile_path)

        try:
            await self.coordinator.hass.async_add_executor_job(atomic_write)
        except Exception:
            self.coordinator.engine.profiles = type(profiles)(previous)
            raise
        self.selected = self.session.name
        await self.cancel()


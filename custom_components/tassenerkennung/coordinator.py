"""One snapshot and one CPU inference for all entities."""

import asyncio
import json
from hashlib import sha256
from datetime import timedelta
import logging
from pathlib import Path
import time

from homeassistant.components.camera import async_get_image
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .vision import Settings, Vision, make_reference, open_image, placeholder
from .backend import LocalDetector
from .tracking import add_landmarks, locate
from .vessel_profiles import VesselProfiles
from .camera_geometry import rectify
from .learning import LearningManager

LIGHTS = ["switch.coffee_station_light_1", "switch.coffee_station_light_2"]

_LOGGER = logging.getLogger(__name__)


class CupCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.settings = Settings.from_dict({**entry.data, **entry.options})
        super().__init__(hass, _LOGGER, config_entry=entry, name="Tassenerkennung",
                         update_interval=timedelta(seconds=self.settings.interval))
        self.entry = entry
        self.engine = None
        self.reference = None
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.position", private=True)
        self.lock = asyncio.Lock()
        self.expiry_cancel = None
        self.latest_content = None
        self.last_capture = None
        self.last_capture_mono = None
        self.frame_hash = None
        self.frame_changed_mono = None
        self.failure_since = None
        self.healthy_since = None
        self.recovery_started = None
        self.recovery_shifts = []
        self.camera_candidate = None
        self.camera_checks = 0
        self.recovery_state = "Bereit"
        self.recovery_last = None
        self.absent_since = None
        self.empty_confirmed = False
        self.placed_at = None
        self.vessel_store=Store(hass,1,f"{DOMAIN}.{entry.entry_id}.vessel",private=True)
        self.saved_lifecycle=None
        self.recovery_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tracking", private=True)
        self.profile_backup_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.profile_backup", private=True)
        self.lights = LIGHTS
        self.learning = None
        self.source_camera_candidate = self.settings.camera
        self.data = {"observation": None, "status": "Warte auf Kamerabild", "jpeg": None,
                     "captured_at": None, "fresh": False, "error": None}

    async def async_initialize(self):
        self.data["jpeg"] = await self.hass.async_add_executor_job(placeholder)
        backend_config = Path(self.hass.config.path(".storage", "tassenerkennung-backend.json"))
        detector = await self.hass.async_add_executor_job(LocalDetector, backend_config)
        self.engine = Vision(detector, self.settings)
        profile_path = Path(self.hass.config.path(".storage", "tassenerkennung.profile-references.json"))
        def load_profiles():
            if not profile_path.exists(): return None
            return VesselProfiles(json.loads(profile_path.read_text()))
        self.engine.profiles = await self.hass.async_add_executor_job(load_profiles)
        if not self.engine.profiles:
            raise HomeAssistantError("Becherprofile fehlen")
        self.learning = LearningManager(self, profile_path, self.profile_backup_store)
        if self.engine.profiles:
            self.engine.profiles.restore_lifecycle(await self.vessel_store.async_load())
            self.saved_lifecycle=self.engine.profiles.lifecycle
        reference = await self.store.async_load()
        if reference and reference.get("fingerprint") == self.settings.fingerprint:
            self.reference = reference
        tracking = await self.recovery_store.async_load() or {}
        self.recovery_state = tracking.get("status", "Bereit") if tracking.get("algorithm_version")==2 else "Bereit"
        self.recovery_last = tracking.get("zeitpunkt")
        if self.recovery_state == "Suche läuft": self.recovery_state = "fehlgeschlagen"
        self.entry.async_on_unload(async_track_state_change_event(
            self.hass, [self.settings.camera, *LIGHTS], self._source_changed))
        self.entry.async_on_unload(self._cancel_expiry)
        await self.async_refresh()

    @property
    def ttl(self):
        return max(5, self.settings.interval * 2)

    @property
    def fresh(self):
        return bool(self.data.get("fresh") and self.last_capture_mono is not None
                    and time.monotonic() - self.last_capture_mono <= self.ttl)

    @callback
    def _source_changed(self, event):
        state = event.data.get("new_state")
        if event.data.get("entity_id") in LIGHTS:
            # A changed lighting condition is a new opportunity, not camera movement.
            old=event.data.get("old_state")
            if state and state.state=="on" and (old is None or old.state!="on"):
                self.failure_since=None
                if self.recovery_state=="fehlgeschlagen": self.recovery_state="Bereit"
            return
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._invalidate("Kamera nicht verfügbar")

    @callback
    def _cancel_expiry(self):
        if self.expiry_cancel:
            self.expiry_cancel()
            self.expiry_cancel = None

    @callback
    def _invalidate(self, message):
        self._cancel_expiry()
        self.empty_confirmed = False
        self.absent_since = None
        if self.engine:
            self.engine.confirmation.reset()
            self.engine.last_positive = None
            if self.engine.profiles: self.engine.profiles.invalidate()
        # Never serve an old green detection after a failure. None uses the clean placeholder.
        self.async_set_updated_data({"observation": None, "status": message, "jpeg": None,
                                     "captured_at": self.last_capture, "fresh": False, "error": message})

    async def _capture(self):
        source = self.hass.states.get(self.settings.camera)
        if not source or source.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            raise HomeAssistantError("Kamera nicht verfügbar")
        image = await async_get_image(self.hass, self.settings.camera, timeout=10, width=1280, height=720)
        if not image.content:
            raise HomeAssistantError("Leeres Kamerabild")
        return image.content

    async def _async_update_data(self):
        async with self.lock:
            try:
                update_started=time.monotonic()
                content = await self._capture()
                capture_ms=(time.monotonic()-update_started)*1000
                captured_at, captured_mono = dt_util.utcnow(), time.monotonic()
                digest = sha256(content).hexdigest()
                if digest == self.frame_hash:
                    # Cached JPEGs are not new observations and cannot build confirmations.
                    if self.frame_changed_mono is None or captured_mono-self.frame_changed_mono > self.ttl:
                        raise HomeAssistantError("Kamera liefert kein neues Bild")
                    return self.data
                self.frame_hash, self.frame_changed_mono = digest, captured_mono
                if not all(self.hass.states.is_state(e,"on") for e in LIGHTS):
                    self.engine.confirmation.reset();self.engine.last_positive=None
                    if self.engine.profiles:self.engine.profiles.invalidate()
                    self.failure_since=None;self.healthy_since=None
                    self.absent_since=None;self.empty_confirmed=False
                    self._cancel_expiry()
                    self.last_capture,self.last_capture_mono=captured_at,captured_mono
                    return {"observation":None,"status":"Arbeitsflächenlicht für Erkennung benötigt","jpeg":None,
                            "captured_at":captured_at,"fresh":True,"error":None,"capture_ms":capture_ms}
                await self._recover_if_needed(content, captured_mono)
                if self.reference and self.reference.get("camera_transform"):
                    content=await self.hass.async_add_executor_job(rectify,content,self.reference)
                observation, jpeg = await self.hass.async_add_executor_job(
                    self.engine.evaluate, content, self.reference, captured_mono)
                if self.engine.profiles and self.engine.profiles.lifecycle != self.saved_lifecycle:
                    await self.vessel_store.async_save(self.engine.profiles.lifecycle)
                    self.saved_lifecycle=self.engine.profiles.lifecycle
                # An inference longer than the freshness window cannot be a current observation.
                if time.monotonic() - captured_mono > self.ttl:
                    raise HomeAssistantError("Bildverarbeitung zu langsam")
                source = self.hass.states.get(self.settings.camera)
                if not source or source.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    raise HomeAssistantError("Kamera nicht verfügbar")
                self.latest_content = content
                self.last_capture, self.last_capture_mono = captured_at, captured_mono
                self._cancel_expiry()
                self.expiry_cancel = async_call_later(self.hass, max(.1, self.ttl - (time.monotonic()-captured_mono)),
                    lambda _: self._invalidate("Kamerabild veraltet"))
                if observation.alignment.ok:
                    self.failure_since = None
                    if self.healthy_since is None: self.healthy_since = captured_mono
                    if captured_mono-self.healthy_since >= 60 and self.recovery_state != "Bereit":
                        await self._tracking_status("Bereit")
                else:
                    self.healthy_since = None
                    if self.failure_since is None: self.failure_since = captured_mono
                empty=observation.absent
                if self.engine.profiles:
                    empty=empty and observation.vessel_type=="leer" and observation.vessel_current_confirmed
                if empty:
                    if self.engine.profiles and self.engine.profiles.since is not None:
                        self.absent_since=captured_at-timedelta(seconds=max(0,captured_mono-self.engine.profiles.since))
                    elif self.absent_since is None:self.absent_since=captured_at
                    if (captured_at-self.absent_since).total_seconds() >= 5:self.empty_confirmed=True
                else:self.absent_since=None
                if not observation.alignment.ok or observation.present is None:
                    self.empty_confirmed = False
                if observation.current_confirmed and self.empty_confirmed:
                    self.placed_at = captured_at
                    self.empty_confirmed = False
                return {"observation": observation, "status": observation.status, "jpeg": jpeg,
                        "captured_at": captured_at, "fresh": True, "error": None, "capture_ms":capture_ms,
                        "total_ms":(time.monotonic()-update_started)*1000}
            except Exception as error:  # A native-model error must also clear the previous positive result.
                self.engine.confirmation.reset()
                self.engine.last_positive = None
                if self.engine.profiles: self.engine.profiles.invalidate()
                self.absent_since = None
                self.empty_confirmed = False
                if self.recovery_state == "Suche läuft" and self.recovery_started is not None and time.monotonic()-self.recovery_started >= 15:
                    await self._tracking_status("fehlgeschlagen")
                self._cancel_expiry()
                _LOGGER.debug("Cup image unavailable: %s", type(error).__name__)
                return {"observation": None, "status": "Kein auswertbares Kamerabild", "jpeg": None,
                        "captured_at": self.last_capture, "fresh": False, "error": type(error).__name__}

    async def _tracking_status(self, status):
        self.recovery_state = status
        self.recovery_last = dt_util.utcnow().isoformat()
        await self.recovery_store.async_save({"status": status, "zeitpunkt": self.recovery_last,"algorithm_version":2})

    async def _recover_if_needed(self, content, stamp):
        """One bounded attempt per lost-position episode; never move the physical camera."""
        if not self.reference or not self.reference.get("landmarks"):
            return
        if self.recovery_state == "Bereit" and self.failure_since is not None and stamp-self.failure_since >= 6:
            # Persist before attempting so reloads cannot repeat an unresolved attempt.
            await self._tracking_status("Suche läuft")
            self.recovery_started = stamp
            self.recovery_shifts = []
            self.camera_candidate=None
            self.camera_checks=0
            self.engine.confirmation.reset()
            self.engine.last_positive = None
            try:
                await self.hass.services.async_call("switch", "turn_on", {"entity_id": LIGHTS}, blocking=True)
            except Exception:
                await self._tracking_status("fehlgeschlagen")
            return
        if self.recovery_state != "Suche läuft" or self.recovery_started is None:
            return
        if stamp-self.recovery_started < 5: return
        if not all(self.hass.states.is_state(e,"on") for e in LIGHTS):
            await self._tracking_status("fehlgeschlagen"); return
        if self.reference.get("camera_context") and hasattr(self.engine.detector,"register_camera"):
            result=await self.hass.async_add_executor_job(self.engine.detector.register_camera,content,self.reference,self.camera_candidate)
            if result.get("ok"):
                matrix=result.get("matrix")
                if self.camera_candidate is None:
                    self.camera_candidate=matrix;self.camera_checks=1
                else:self.camera_checks+=1
                if self.camera_checks>=3:
                    self.reference={**self.reference,"camera_transform":self.camera_candidate,
                                    "offset":result["offset"],"camera_registration":{k:v for k,v in result.items() if k not in ["matrix"]}}
                    await self.store.async_save(self.reference)
                    self.engine.confirmation.reset();self.engine.last_positive=None
                    if self.engine.profiles:self.engine.profiles.invalidate()
                    await self._tracking_status("erfolgreich")
                    return
            else:
                self.camera_candidate=None;self.camera_checks=0
            if stamp-self.recovery_started>=25:await self._tracking_status("fehlgeschlagen")
            return
        def job(): return locate(open_image(content), self.settings, self.reference, recovery=True)
        alignment = await self.hass.async_add_executor_job(job)
        if alignment.ok:
            shift=alignment.shift
            if self.recovery_shifts and max(abs(shift[i]-self.recovery_shifts[-1][i]) for i in (0,1))>2:
                self.recovery_shifts=[]
            self.recovery_shifts.append(shift)
            if len(self.recovery_shifts)>=3:
                self.reference={**self.reference,"offset":list(shift)}
                await self.store.async_save(self.reference)
                self.engine.confirmation.reset()
                self.engine.last_positive=None
                await self._tracking_status("erfolgreich")
                return
        else: self.recovery_shifts=[]
        if stamp-self.recovery_started >= 15:
            await self._tracking_status("fehlgeschlagen")

    async def async_confirm_position(self):
        """Only an explicit user action creates/replaces the position reference."""
        async with self.lock:
            content = await self._capture()
            transform=self.reference.get("camera_transform") if self.reference else None
            if transform:content=await self.hass.async_add_executor_job(rectify,content,self.reference)
            def reference_job():
                image = open_image(content)
                return add_landmarks(make_reference(image, self.settings), image, self.settings)
            try:
                reference = await self.hass.async_add_executor_job(reference_job)
            except ValueError as error:
                raise HomeAssistantError("Die Maschinenfläche ist zu dunkel oder nicht eindeutig. Bitte den Bildbereich prüfen.") from error
            if transform:reference["camera_transform"]=transform
            await self.store.async_save(reference)
            self.reference = reference
            await self._tracking_status("Bereit")
            self.failure_since = None
            self.healthy_since = None
            self.engine.confirmation.reset()
            self.engine.last_positive = None
        await self.async_request_refresh()

    async def async_close(self):
        self._cancel_expiry()
        self.latest_content = None
        self.reference = None

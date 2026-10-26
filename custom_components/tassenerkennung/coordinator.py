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

LIGHTS = ["switch.kueche_schrankbeleuchtung_links", "switch.kueche_schrankbeleuchtung_rechts"]

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
        self.raw_content = None
        self.active_settings = self.settings
        self.tracking_info = {}
        self.tracking_count = 0
        self.tracking_previous = None
        self.tracking_last_body = None
        self.candidate_hint = False
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
        needs_write=not await self.hass.async_add_executor_job(profile_path.exists)
        def load_profiles():
            if not profile_path.exists():return VesselProfiles.empty(self.settings.camera)
            return VesselProfiles(json.loads(profile_path.read_text()))
        self.engine.profiles = await self.hass.async_add_executor_job(load_profiles)
        if self.engine.profiles.data.get('version',2)<3:
            # Old templates were tied to the former fixed crop. Never silently
            # authorize them against the new machine-relative geometry.
            await self.profile_backup_store.async_save(self.engine.profiles.export())
            self.engine.profiles=VesselProfiles.empty(self.settings.camera,names=self.engine.profiles.names)
            needs_write=True
        if needs_write:
            def save_initial():
                import os
                temporary=profile_path.with_suffix('.initial.tmp')
                temporary.write_text(json.dumps(self.engine.profiles.export(),ensure_ascii=False,separators=(',',':')))
                os.chmod(temporary,0o600);os.replace(temporary,profile_path)
            await self.hass.async_add_executor_job(save_initial)
        self.learning = LearningManager(self, profile_path, self.profile_backup_store)
        if self.engine.profiles:
            self.engine.profiles.restore_lifecycle(await self.vessel_store.async_load())
            self.saved_lifecycle=self.engine.profiles.lifecycle
        reference = await self.store.async_load()
        if reference and (reference.get("fingerprint") == self.settings.fingerprint or reference.get("machine_model")):
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
        # A temporary dark/stale frame must not erase a previously verified
        # empty tray. It is only an arming fact, never a beverage permission.
        # A confirmed vessel, camera change or calibration clears it elsewhere.
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
        from .pipeline import update
        async with self.lock:
            try:
                return await update(self)
            except Exception as error:
                self.engine.confirmation.reset()
                self.engine.last_positive = None
                if self.engine.profiles:self.engine.profiles.invalidate()
                self.tracking_info={"ok":False,"confirmed":False,"reason":type(error).__name__}
                self.tracking_count=0
                self._cancel_expiry()
                messages={"camera_image_not_updating":"Kamera liefert derzeit kein neues Bild",
                          "processing_too_slow":"Bildverarbeitung ist zu langsam"}
                message=messages.get(str(error),"Kamerabild kann gerade nicht geprüft werden")
                _LOGGER.debug("Cup pipeline unavailable: %s: %s",type(error).__name__,str(error)[:160])
                return {"observation":None,"status":message,"jpeg":None,"raw_jpeg":self.raw_content,
                        "captured_at":self.last_capture,"fresh":False,"error":type(error).__name__}

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
        if self.reference and self.reference.get('machine_model'):
            self.tracking_previous=None;self.tracking_count=0
            for _ in range(4):
                await self.async_request_refresh()
                if self.tracking_info.get('confirmed'):return
                await asyncio.sleep(.7)
            raise HomeAssistantError(self.data.get('status','Maschine konnte nicht bestätigt werden'))
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

    async def async_calibrate_machine(self,body,rotation=0):
        """Explicit visual setup after a camera swap; never reuse old cup permission."""
        if self.learning.active:raise HomeAssistantError('Bitte den Einlernvorgang zuerst abbrechen')
        from io import BytesIO
        from PIL import Image
        import os
        from .camera_geometry import camera_context
        async with self.lock:
            content=await self._capture()
            def build():
                image=open_image(content)
                if rotation==180:image=image.rotate(180)
                reference={'fingerprint':self.settings.fingerprint,'source_size':list(image.size),
                           'camera_context':camera_context(image,self.settings),'baseline_camera':self.settings.camera}
                model=self.engine.detector.setup_machine(reference,body)
                reference['machine_model']=model
                reference['camera_transform']=[[-1,0,image.width-1],[0,-1,image.height-1],[0,0,1]] if rotation==180 else [[1,0,0],[0,1,0],[0,0,1]]
                return reference
            try:reference=await self.hass.async_add_executor_job(build)
            except ValueError as error:raise HomeAssistantError(str(error)) from error
            old_reference=self.reference;old_profiles=self.engine.profiles
            previous=old_profiles.export();await self.profile_backup_store.async_save(previous)
            payload={'version':3,'camera':self.settings.camera,'source_size':reference['source_size'],
                     'zone_relative_box':[0,0,1,1],'names':dict(old_profiles.names),'samples':{'leer':[]},'automatic_beverages':False}
            def write(value):
                path=self.learning.profile_path;temporary=path.with_suffix('.calibration.tmp')
                temporary.write_text(json.dumps(value,separators=(',',':'),ensure_ascii=False));os.chmod(temporary,0o600);os.replace(temporary,path)
            self._invalidate('Maschinenreferenz wird eingerichtet')
            try:
                await self.hass.async_add_executor_job(write,payload)
                await self.store.async_save(reference)
            except Exception:
                await self.hass.async_add_executor_job(write,previous)
                if old_reference:await self.store.async_save(old_reference)
                raise
            self.reference=reference;self.engine.profiles=VesselProfiles(payload)
            self.tracking_count=0;self.tracking_previous=None;self.tracking_last_body=None
            self.empty_confirmed=False;self.absent_since=None;self.placed_at=None
            self.saved_lifecycle=self.engine.profiles.lifecycle;await self.vessel_store.async_save(self.saved_lifecycle)
            await self._tracking_status('Bereit')
        await self.async_request_refresh()

    async def async_close(self):
        if self.learning and self.learning.active:await self.learning.cancel(refresh=False)
        self._cancel_expiry()
        self.latest_content = None
        self.reference = None

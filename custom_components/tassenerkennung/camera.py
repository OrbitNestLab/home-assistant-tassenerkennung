"""Annotated camera. Requests read the shared result and do not trigger inference."""

from homeassistant.components.camera import Camera,async_get_image

from .entity import CupEntity
from .vision import placeholder
from io import BytesIO
from PIL import Image


async def async_setup_entry(hass, entry, async_add_entities):
    empty = await hass.async_add_executor_job(placeholder, "Kein aktuelles Prüfbild")
    async_add_entities([CupCamera(entry.runtime_data, empty),SourcePreview(entry.runtime_data,empty),TrackingPreview(entry.runtime_data,empty)])


class CupCamera(CupEntity, Camera):
    def __init__(self, coordinator, empty):
        Camera.__init__(self)
        CupEntity.__init__(self, coordinator, "camera", "Prüfbild", "mdi:camera-outline")
        self._empty = empty

    async def async_camera_image(self, width=None, height=None):
        return self.coordinator.data.get("jpeg") if self.coordinator.fresh and self.coordinator.data.get("jpeg") else self._empty


class SourcePreview(CupEntity,Camera):
    def __init__(self,coordinator,empty):
        Camera.__init__(self);CupEntity.__init__(self,coordinator,"source_preview","Kameravorschau","mdi:camera-switch")
        self._empty=empty

    async def async_camera_image(self,width=None,height=None):
        if self.coordinator.source_camera_candidate==self.coordinator.settings.camera and self.coordinator.raw_content:
            if abs(self.coordinator.tracking_info.get('angle',0))>135:
                def rotate():
                    image=Image.open(BytesIO(self.coordinator.raw_content)).convert('RGB').rotate(180)
                    output=BytesIO();image.save(output,format='JPEG',quality=88);return output.getvalue()
                return await self.hass.async_add_executor_job(rotate)
            return self.coordinator.raw_content
        try:
            image=await async_get_image(self.hass,self.coordinator.source_camera_candidate,timeout=10,width=1280,height=720)
            return image.content or self._empty
        except Exception:
            return self._empty


class TrackingPreview(CupEntity,Camera):
    def __init__(self,coordinator,empty):
        Camera.__init__(self);CupEntity.__init__(self,coordinator,'tracking_preview','Tracking','mdi:crosshairs-gps')
        self._empty=empty

    async def async_camera_image(self,width=None,height=None):
        return self.coordinator.data.get('tracking_jpeg') if self.coordinator.fresh and self.coordinator.data.get('tracking_jpeg') else self._empty

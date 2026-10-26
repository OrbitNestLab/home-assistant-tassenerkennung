"""Local cup recognition. No services for brewing coffee are provided."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import probatio as vol

from .coordinator import CupCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.CAMERA, Platform.BUTTON,
             Platform.SELECT, Platform.TEXT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CupCoordinator(hass, entry)
    await coordinator.async_initialize()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    register_services(hass)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_close()
        return True
    return False


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


def register_services(hass):
    if not hass.services.has_service('tassenerkennung','calibrate_machine'):
        async def calibrate(call):
            entries=[e for e in hass.config_entries.async_entries('tassenerkennung')
                     if getattr(e,'runtime_data',None) and e.runtime_data.settings.camera==call.data['camera']]
            if len(entries)!=1:raise HomeAssistantError('Die aktive Kamera konnte nicht eindeutig zugeordnet werden')
            await entries[0].runtime_data.async_calibrate_machine(call.data['body'],call.data['rotation'])
        hass.services.async_register('tassenerkennung','calibrate_machine',calibrate,
            schema=vol.Schema({vol.Required('camera'):vol.Match(r'^camera\.[a-z0-9_]+$'),
                vol.Required('body'):vol.All([vol.Coerce(float)],vol.Length(min=4,max=4)),
                vol.Optional('rotation',default=0):vol.In([0,180])}))

"""Camera and region configuration; all coordinates are full-image percentages."""

import probatio as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

from .const import DEFAULTS, DOMAIN
from .vision import Settings


def schema(defaults, advanced=False, excluded=None):
    def number(key, minimum, maximum, step=1):
        return (vol.Required(key, default=defaults[key]), selector.NumberSelector(
            selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX)))
    fields = {
        vol.Required("camera", default=defaults["camera"]): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="camera", exclude_entities=excluded or [])),
        **dict([number("interval", 1, 60), number("confidence", .4, .95, .01), number("confirmations", 2, 8)]),
    }
    if advanced:
        for region in ("crop", "zone", "anchor"):
            for side in ("left", "top", "right", "bottom"):
                key = f"{region}_{side}"
                fields.update(dict([number(key, 0, 100, .1)]))
    return vol.Schema(fields)


def own_cameras(hass):
    return [entity.entity_id for entity in er.async_get(hass).entities.values()
            if entity.platform == DOMAIN and entity.domain == "camera"]


def validate(hass, values):
    Settings.from_dict(values)
    state = hass.states.get(values["camera"])
    if not state or state.domain != "camera" or values["camera"] in own_cameras(hass):
        raise ValueError("camera_missing")


class CupConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CupOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        values = {**DEFAULTS, **(user_input or {})}
        if user_input is not None:
            try:
                validate(self.hass, values)
                await self.async_set_unique_id(values["camera"])
                self._abort_if_unique_id_configured()
            except ValueError as error:
                errors["base"] = str(error)
            else:
                return self.async_create_entry(title="Tassenerkennung", data=values)
        return self.async_show_form(step_id="user", data_schema=schema(values, excluded=own_cameras(self.hass)), errors=errors)


class CupOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        values = {**DEFAULTS, **self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        if user_input is not None:
            try:
                validate(self.hass, values)
                if any(entry.entry_id != self.config_entry.entry_id and
                       entry.options.get("camera", entry.data.get("camera")) == values["camera"]
                       for entry in self.hass.config_entries.async_entries(DOMAIN)):
                    raise ValueError("already_configured")
            except ValueError as error:
                errors["base"] = str(error)
            else:
                return self.async_create_entry(title="", data=values)
        return self.async_show_form(step_id="init", data_schema=schema(values, advanced=True, excluded=own_cameras(self.hass)), errors=errors)

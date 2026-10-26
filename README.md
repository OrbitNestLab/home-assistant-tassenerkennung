# Tassenerkennung for Home Assistant

Local cup recognition for a coffee-machine camera, packaged as a Home Assistant custom integration plus a private Ingress app. Version 0.7.0 of the integration and 0.4.1 of the app are included.

The project detects whether a vessel is positioned below the outlet, keeps stale or uncertain images fail-safe, learns several views of a cup, and can switch to another Home Assistant camera from its own sidebar UI. It deliberately contains **no service that starts a beverage**. Any coffee-machine automation must be created and reviewed separately.

## Highlights

- Fully local image processing with YOLOX S and ONNX Runtime.
- Automatic coffee-machine and outlet localization with perspective correction and a stable canonical zoom.
- One-second coordinator with freshness checks and temporal confirmation.
- Processed camera image with visible detection/position overlays.
- Guided learner: empty counter, front, rotated and offset views.
- Nine fresh vessel samples before a profile can be saved.
- Training lock makes recognition unavailable while a profile is being learned.
- Quick camera swap: select, preview, apply, automatically locate the machine/outlet, align and retrain.
- Observable background jobs in the Ingress UI instead of fragile long-running browser requests.
- Profile-v3 comparison with empty-counter subtraction, shape/colour checks and separation from the second-best profile.
- Old profile data is backed up before a write and never silently trusted for a different camera.
- Ingress-only management UI; no published host port.
- Fixed Home Assistant API allowlist; the app cannot call arbitrary services.
- No direct coffee, milk or beverage action anywhere in the integration or app.

## Components

- `custom_components/tassenerkennung/` — Home Assistant integration 0.7.0.
- `addon/` — local Supervisor app 0.4.1 with the model and responsive sidebar UI.
- `tests/` — local safety, geometry, tracking, learner and HTTP tests.
- `examples/nachbestellung-6-15.yaml` — Marcel's reviewed external Home Assistant automation: after a completed coffee that day, a newly placed confirmed coffee cup requests the existing safe coffee automation from 06:00–14:59; from 15:00–15:59 it only powers on an off machine. Adapt every entity before use.

## Installation

1. Copy `custom_components/tassenerkennung` to `/config/custom_components/`.
2. Copy `addon` to `/addons/tassenerkennung/` on a Home Assistant OS/Supervised host.
3. Reload the local add-on store, install **Tassenerkennung**, configure a random API key of at least 32 characters, start it and enable its sidebar entry.
4. Create `/config/.storage/tassenerkennung-backend.json` on the Home Assistant host:

   ```json
   {
     "url": "http://local-tassenerkennung:8080",
     "token": "THE-SAME-RANDOM-KEY"
   }
   ```

5. Restart Home Assistant and add the **Tassenerkennung** integration from **Settings → Devices & services**.
6. Select the source camera and adjust crop, vessel zone and fixed machine reference in the integration options.
7. Replace the two example kitchen-light entity IDs in `coordinator.py` and `button.py` with the lights that provide stable colour illumination at your machine. The original installation uses two worktop lights; IR-only night images were not reliable enough.
8. Confirm alignment, then use the sidebar learner. Do not connect a beverage automation before the empty counter and the required vessel profiles have been tested in real lighting.

## Switching cameras

Add the replacement camera to Home Assistant first. In the Tassenerkennung sidebar app, choose it under **Kamera wechseln**, inspect the raw preview and press **Kamera übernehmen**. Turn on the normal worktop lighting, confirm the new alignment, and relearn every profile needed by an external automation. Profiles from the old view remain backed up but are not silently reused.

## Safety model

Recognition is intentionally unavailable when the image is stale, the camera is offline, alignment is not trustworthy, the target area is blocked, the training mode is active, or too few distinct observations exist. A held UI label never grants permission to an automation.

The integration and app never start a beverage themselves. The optional example automation is deliberately external and **can request a beverage** through an existing Home Assistant automation. Review and adapt it before importing. A beverage automation must require at least:

- a current positive vessel sensor;
- a freshly confirmed matching vessel type;
- a valid machine reference;
- an image timestamp inside a short window;
- known lighting;
- coffee-machine readiness and its native safety interlocks.

Do not publish camera snapshots, profile reference files, Home Assistant tokens or the backend API key.

## Tests

Run:

```text
python -m pytest tests -q
```

The released source passed 54 local tests plus isolated Home Assistant scenarios covering camera loss, stale frames, automatic 180-degree correction, machine/outlet tracking, learning, profile resets and the API allowlist. No real beverage action was triggered during those tests.

## Licences

Project code is MIT licensed. The bundled YOLOX S model and derived inference code are covered by Megvii's Apache-2.0 licence in `addon/LICENSE-YOLOX` and the corresponding notices inside the integration. Roboto is covered by the SIL Open Font License included with the integration.

This is an independent community project and is not affiliated with Home Assistant, Eve, Siemens, Bosch, Roborock or Megvii.

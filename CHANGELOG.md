# Changelog

## Integration 0.7.0 / App 0.4.1

- Automatically locate and track the known coffee-machine body and outlet, including perspective correction and 180-degree camera orientation.
- Render a canonical close-up for recognition while retaining source and tracking previews for diagnosis.
- Replace the old fixed mini-crop profiles with profile-v3 samples that subtract the empty counter and compare shape, colour and second-best separation.
- Add observable background setup/learning jobs to the Ingress UI and make camera changes an explicit guided workflow.
- Keep a verified empty-tray arming state across a temporary dark, stale or unavailable frame without ever preserving a current beverage authorization.
- Expand fail-safe tests for missing/covered machines, blocked outlets, learning boundaries, HTTP framing and the real local model.

## Integration 0.6.0 / App 0.3.3

- Fix all management actions behind Supervisor Ingress: disable streaming so the bounded JSON handler receives Content-Length. Reproduced the failure with chunked POSTs (400) versus buffered POSTs (200).
- Return an explicit 411 error for unframed requests and distinguish action failures from connection failures.
- Earlier versions 0.3.1 and 0.3.2 improved warning and path handling but did not resolve the actual action-delivery fault.

- Resolve all status, action and camera requests against the actual Home Assistant Ingress path, including URLs without a trailing slash.
- Clear the stale Home Assistant connection warning after a successful status refresh.

- Added a standalone Home Assistant Ingress interface.
- Added a guided four-stage vessel learner with nine positive samples and an empty-counter reference.
- Added user-named vessel profiles without automatic beverage actions.
- Added a training lock for all recognition outputs.
- Added quick camera selection, raw preview, safe apply and alignment workflow.
- Invalidated image samples when camera/source size changes while preserving backed-up profile names.
- Reduced dashboard load by fetching Home Assistant states in one request and throttling raw previews.
- Kept the authenticated model API backwards compatible.

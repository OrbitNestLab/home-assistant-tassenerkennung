# Changelog

## Integration 0.6.0 / App 0.3.1

- Clear the stale Home Assistant connection warning after a successful status refresh.

- Added a standalone Home Assistant Ingress interface.
- Added a guided four-stage vessel learner with nine positive samples and an empty-counter reference.
- Added user-named vessel profiles without automatic beverage actions.
- Added a training lock for all recognition outputs.
- Added quick camera selection, raw preview, safe apply and alignment workflow.
- Invalidated image samples when camera/source size changes while preserving backed-up profile names.
- Reduced dashboard load by fetching Home Assistant states in one request and throttling raw previews.
- Kept the authenticated model API backwards compatible.

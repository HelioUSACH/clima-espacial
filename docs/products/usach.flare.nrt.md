# Model card: `usach.flare.nrt`

_Stub — to be completed by the model's owner before this product leaves
`beta`. See `docs/models/MODEL-CARD-TEMPLATE.md` for the full structure this
should grow into._

## Summary

Near-real-time solar flare detector built on SDO/AIA imagery. **Beta**
(D-036): public from day one with that label; a known low-probability
signal artifact is still under diagnosis, and severity/confidence
sub-scores are intentionally not republished by the portal yet
(`modules/flare/adapters.py`) — summary, inputs, training data and
performance-vs-baseline sections to follow from the model's own repository
(`HelioUSACH/flare-nrt`).

## Maintainers / links

Producer repository: `HelioUSACH/flare-nrt` (integration contract:
`docs/models/INTEGRATION.md`; consumer-side module: `modules/flare/`).

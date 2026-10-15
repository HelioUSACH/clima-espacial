# Integrating a model

Models are built and owned in **their own repository** (D-044) — weights
ship as a release asset there, never committed here, and never developed in
an OneDrive-synced folder (a shared folder written by two hosts at once has
already corrupted a model's cache once; see that repo's own audit notes).
This repo never imports a model's code. Integrating one is exactly the
product contract everything else in this repo already follows:

1. **A manifest** under `modules/<name>/manifests/` (docs/ARCHITECTURE.md
   §4.1) — `kind: event` for a detector/classifier, `maturity: experimental`
   or `beta` until there's a model card with real skill numbers (never
   `operational` for a model still being validated).
2. **A producer** that publishes through `core/swxkit.py`. Two shapes, pick
   whichever matches how the model actually runs:
   - **Same venv, in-process**: the model's own training/inference repo
     exposes a Django management-command-friendly entry point, or this repo
     adds a small collector that imports it. Rare — most models are a
     separate service (below).
   - **Separate service, polled** (the common case — `modules/flare/` is the
     worked example): the model runs as its own long-running service (here,
     a FastAPI process) exposing its own status/results endpoint. A small
     module in *this* repo (`client.py` + `adapters.py` + `collectors.py`,
     same shape as every other adapter — docs/modules/README.md) polls that
     endpoint on a systemd timer and republishes the result as an event via
     `swxkit.publish_event`. This repo never depends on the model repo's
     code, only on its HTTP contract.

   A third shape — the model repo pushing to an HTTP ingest endpoint on this
   side instead of being polled — is in the design (docs/ARCHITECTURE.md
   §4.2) but not built until a producer actually needs it (M2, D-048).
3. **A model card** (`MODEL-CARD-TEMPLATE.md` in this directory) at the path
   the manifest's `model_card` field points to — required before the
   product's maturity can move past `experimental`.
4. **Freshness and honesty.** Set `stale_after` to something a bit looser
   than the producer's own staleness threshold if it publishes one (so this
   repo's heartbeat tracks, not fights, the producer's own notion of
   "stale") — `modules/flare/manifests/nrt.yaml` picks `10m` to match
   flare-nrt's own `STALE_AFTER_SEC`. Never republish a field the model
   itself flags as exploratory/low-confidence as if it were a validated
   signal (01-AUDIT.md §3.6 — `modules/flare/adapters.py` deliberately drops
   `severity` for exactly this reason). A product whose producer isn't
   running yet (or isn't stable) must degrade to "en preparación"/stale
   automatically, from its real heartbeat — never a hard-coded flag in the
   view (see `web/views.py::_flare_card_ready`, D-036).

## Worked example: `modules/flare/`

Polls `HelioUSACH/flare-nrt`'s internal `/api/status` (never exposed by
nginx — docs/ARCHITECTURE.md §6) every minute and republishes
`probability`/`flare_detected`/`location` as a `usach.flare.nrt` event. If
the service isn't reachable, the collector marks the heartbeat `error`
instead of crashing, and the portal's beta card falls back to "en
preparación" (D-036) — launch was never blocked on this model being ready.

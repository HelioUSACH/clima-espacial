# Integration sheet: <model name>

_Copy to `INTEGRATION.md` at the root of the model's repository and fill in.
One page; "unknown, because …" is an acceptable answer._

| | |
|---|---|
| Model / product id | e.g. `usach.flare.nrt` |
| Kind | event (detection/classification) or forecast (predicts an observed quantity) |
| Target (forecasts) | portal product + field, horizons, uncertainty |
| Owner(s) | names, GitHub handles |
| Repo, current tag | |
| Run cadence | how often it produces a result |
| Inputs | for each: source, URL, cadence, typical latency, licence, volume per day |
| New data the portal must collect | none / list |
| Latest-result endpoint | URL path, and the response fields (name, type, units, meaning) |
| Freshness | how status `stale` is decided |
| Resources | peak RAM, CPU seconds per run, GPU needed (yes/no), disk at steady state |
| Weights | release asset name, size, SHA-256 |
| Baseline it must beat | e.g. persistence, climatology, a published model |
| Known limitations | short list |
| Model card | path |

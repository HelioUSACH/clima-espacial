# modules/kyoto

WDC for Geomagnetism, Kyoto, through its HAPI 3.x server
(`https://wdc.kugi.kyoto-u.ac.jp/hapi`).

| product | dataset | timer | notes |
|---|---|---|---|
| `kyoto.dst` | `hour_dst_realtime` (last 8 h) | every 15 min | NOAA SWPC's `products/kyoto-dst.json` as fallback (D-081) |
| `kyoto.ae` | `min_ae` (best available, last 40 days) | daily 04:10 UT | delayed: Kyoto's HAPI copy runs weeks behind (D-082) |

- `client.py`: `info` and `data` (CSV), with `stop` clamped to the dataset's
  `stopDate` (a later stop is HAPI error 1405), one retry, and a
  User-Agent naming the portal and `SITE_CONTACT_EMAIL`. A failure raises
  `HapiError`, and the collector turns it into a heartbeat, never a crash. No
  `hapiclient` (it pulls in pandas/numpy).
- `adapters.py`: CSV → rows. Columns come from `info`, because HAPI CSV has no
  header. Kyoto stamps bin *centres*; `time_tag` is the interval *start*,
  as for every other product (hourly `hh:29:30` → `hh:00`; a 1-minute stamp
  is already its minute). Fill `99999` → null. `version` = `versionCode`
  (Mm: M 0 realtime, 1 provisional, 2 final). The NOAA mirror's rows get
  `version: -1`, so Kyoto's value for the same hour always wins.
- Both products are revisable (`revise` in their manifests, D-080). Each
  row's `source` is `KYOTO-HAPI` or `NOAA-MIRROR`.
- One-off backfills, which also repair stored rows Kyoto has revised:
  `manage.py backfill_kyoto_dst --days 30` and `manage.py backfill_kyoto_ae
  --since 2026-01-01` (commands in `core/management/commands/`, since a
  module isn't a Django app).

**Terms.** "The WDC Kyoto does not allow commercial applications of the
geomagnetic indices" (`https://wdc.kugi.kyoto-u.ac.jp/wdc/Sec3.html`), and
the real-time data are "only for monitoring, diagnostics, and forecasting".
Both products are shown, never redistributed (`exchange` off, D-028,
D-078), and cite their DOIs (`facts.citation`).

Fixtures: `tests/fixtures/README.md`.

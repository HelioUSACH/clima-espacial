Recorded from `https://wdc.kugi.kyoto-u.ac.jp/hapi` on 2026-10-18:

- `info_hour_dst_realtime.json`, `info_min_ae.json`: `/hapi/info?dataset=…`, unedited.
- `hour_dst_realtime.csv`: `/hapi/data?dataset=hour_dst_realtime&start=2026-10-17T18:00:00Z&stop=2026-10-18T02:00:00Z`, unedited.
- `min_ae.csv`: the first three rows are `/hapi/data?dataset=min_ae&start=2026-10-04T23:50:00Z&stop=2026-10-04T23:53:00Z`.
  The last two are **hand-made** to test the fill value (`99999`, declared in `info_min_ae.json`): one row all fill, one partly.

Vendored copy of [uPlot](https://github.com/leeoniya/uPlot) 1.6.32 (MIT —
see `LICENSE`), chosen over Apache ECharts for M1 (docs/DECISIONS.md D-042):
small, dependency-free, built for exactly this (many dense, multi-day
1-minute series). Copied in rather than loaded from a CDN (D-002).

To update: download `uPlot.iife.min.js`, `uPlot.min.css` and `LICENSE` from
the matching GitHub release and replace these three files; nothing else in
this repo should need to change.

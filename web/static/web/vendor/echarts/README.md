# Apache ECharts (trimmed build)

`echarts.swx.min.js` is a custom ECharts 5.5.1 build containing only what
this portal uses: line/bar charts, grid, tooltip, axisPointer, inside
dataZoom, markLine/markArea/markPoint, legend, toolbox (saveAsImage),
canvas renderer. Built once with esbuild from `entry.js`, vendored as one
plain IIFE file (`window.echarts`) — no runtime build step (D-002).

- Source + rebuild instructions: `entry.js`, `build.sh` (repo root
  `ref/echarts-swx/`, git-ignored — ask Victor for the current copy).
- Size: 597 KB raw, 199 KB gzipped (16-PERF-AND-CHART-ENGINE.md §1).
- Licence: Apache-2.0 (`LICENSE`, this directory).
- Upstream: https://github.com/apache/echarts, v5.5.1.

Rebuild only when upgrading ECharts itself; nothing at runtime depends on
npm or esbuild.

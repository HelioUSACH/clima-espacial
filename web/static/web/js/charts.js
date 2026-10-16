/* Stacked, synced-crosshair chart panels (12-HANDOFF-2026-10-15.md §4).
 * Wires vendored uPlot (web/static/web/vendor/uplot/) to the
 * `/productos/<id>/serie.json` endpoint. No build step, no CDN (D-002):
 * plain ES2017, loaded with <script defer>.
 *
 * serie.json is bucketed server-side (D-056): each field arrives as a mean
 * (the line) plus a per-bucket min/max, drawn as a faint band on the
 * solar-wind panels. A `null` bucket is a real gap and breaks the line.
 *
 * Series colors are CSS variable *names* (e.g. "--chart-s1"), not hex: a
 * <canvas> context can't resolve var(...) the way the DOM can. Every color
 * is resolved ONCE per render into a plain palette object (and again on
 * `swx:themechange`, D-053), never inside a draw hook: getComputedStyle in
 * a hook forced a style recalculation per Kp bar per redraw (16-PERF B5).
 *
 * Loading (16-PERF B1/B2): one AbortController per stack, so a superseded
 * range click is cancelled, and any response whose range is no longer the
 * stack's current one is dropped; responses are kept per range for 60 s, so
 * going back to a range already seen redraws without a request.
 *
 * Kp's bars and every zero/reference/class-band line are hand-drawn in a
 * `hooks.draw` callback against uPlot's public `valToPos`/`ctx` API,
 * instead of uPlot's own (ESM-only, build-step-only) bars path plugin —
 * this project ships no bundler (D-002), so that plugin can't be vendored.
 */
(function () {
  "use strict";

  var CACHE_TTL_MS = 60 * 1000; // matches web/charts.py SERIES_CACHE_SECONDS
  var BAND_ALPHA = 0.16;

  // Kept in sync with web/charts.py KP_SEVERITY_THRESHOLDS/KP_SEVERITY_STATUSES
  // (NOAA's Kp->G mapping folded onto the portal's 4-step status vocabulary).
  var KP_THRESHOLDS = [4, 5, 7];
  var KP_STATUSES = ["ok", "stale", "serious", "error"];

  // ---- palette (B5): resolved once, read many times ----------------------

  var PALETTE_VARS = [
    "--chart-text", "--chart-grid", "--chart-axis", "--chart-ref", "--surface-card",
    "--chart-s1", "--chart-s2", "--chart-s3", "--chart-s4", "--chart-s5", "--chart-s6",
    "--status-ok", "--status-stale", "--status-serious", "--status-error",
  ];
  var palette = {};

  function resolvePalette() {
    var style = getComputedStyle(document.documentElement);
    var next = {};
    PALETTE_VARS.forEach(function (name) {
      next[name] = style.getPropertyValue(name).trim();
    });
    palette = next;
  }

  function color(name) {
    return palette[name] || "";
  }

  // "#rrggbb" / "#rgb" / "rgb(r, g, b)" -> "rgba(r, g, b, a)"; anything else
  // (a named color) is returned as-is rather than guessed at.
  function withAlpha(c, alpha) {
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c);
    if (m) {
      var hex = m[1].length === 3 ? m[1].replace(/./g, "$&$&") : m[1];
      var n = parseInt(hex, 16);
      return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
    }
    m = /^rgba?\(([^)]+)\)$/i.exec(c);
    if (m) {
      var parts = m[1].split(",").slice(0, 3).join(",");
      return "rgba(" + parts + "," + alpha + ")";
    }
    return c;
  }

  function kpSeverityVar(value) {
    if (value == null) return null;
    for (var i = 0; i < KP_THRESHOLDS.length; i++) {
      if (value < KP_THRESHOLDS[i]) return "--status-" + KP_STATUSES[i];
    }
    return "--status-" + KP_STATUSES[KP_STATUSES.length - 1];
  }

  function fmtNum(v) {
    if (v == null) return "";
    var abs = Math.abs(v);
    if (abs !== 0 && (abs < 0.01 || abs >= 1e5)) return v.toExponential(2);
    var digits = abs >= 100 ? 0 : abs >= 1 ? 1 : 2;
    return v.toFixed(digits);
  }

  function lastIndexWithValue(arr) {
    for (var i = arr.length - 1; i >= 0; i--) {
      if (arr[i] != null) return i;
    }
    return -1;
  }

  // ---- UT time formatting (13-AUDIT §3: 24 h, dd/mm in ES, "15 Oct" in EN)

  function pad2(n) {
    return n < 10 ? "0" + n : "" + n;
  }

  function fmtDate(d, i18n) {
    if (i18n.lang === "en") return pad2(d.getUTCDate()) + " " + i18n.months[d.getUTCMonth()];
    return pad2(d.getUTCDate()) + "/" + pad2(d.getUTCMonth() + 1);
  }

  function fmtTime(d) {
    return pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes());
  }

  function timeAxisValues(i18n) {
    return function (u, splits, axisIdx, foundSpace, foundIncr) {
      var prevDay = null;
      return splits.map(function (v) {
        if (v == null) return "";
        var d = new Date(v * 1000);
        var day = d.getUTCFullYear() * 400 + d.getUTCMonth() * 32 + d.getUTCDate();
        var label;
        if (foundIncr >= 86400) {
          label = fmtDate(d, i18n);
        } else if (day !== prevDay) {
          label = fmtTime(d) + "\n" + fmtDate(d, i18n);
        } else {
          label = fmtTime(d);
        }
        prevDay = day;
        return label;
      });
    };
  }

  // Log axes: one tick/grid line per decade only (no minor lines).
  function decadeSplits(u, axisIdx, min, max) {
    var out = [];
    for (var e = Math.ceil(Math.log10(min) - 1e-9); e <= Math.floor(Math.log10(max) + 1e-9); e++) {
      out.push(Math.pow(10, e));
    }
    return out;
  }

  // ---- hand-drawn overlays (hooks.draw) --------------------------------

  function drawHLine(u, value, strokeColor, dashed, label) {
    var top = u.valToPos(value, "y", true);
    if (!(top >= u.bbox.top && top <= u.bbox.top + u.bbox.height)) return;
    var ctx = u.ctx;
    ctx.save();
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 1;
    ctx.setLineDash(dashed ? [5, 4] : []);
    ctx.beginPath();
    ctx.moveTo(u.bbox.left, top);
    ctx.lineTo(u.bbox.left + u.bbox.width, top);
    ctx.stroke();
    if (label) {
      ctx.setLineDash([]);
      ctx.fillStyle = strokeColor;
      ctx.font = "11px system-ui, sans-serif";
      ctx.textBaseline = "bottom";
      ctx.fillText(label, u.bbox.left + u.bbox.width - ctx.measureText(label).width - 4, top - 2);
    }
    ctx.restore();
  }

  function drawLastDatumDots(u, panel) {
    var ctx = u.ctx;
    var surface = color("--surface-card") || "#fff";
    for (var s = 1; s < panel.series.length + 1; s++) {
      var ys = u.data[s];
      var idx = lastIndexWithValue(ys);
      if (idx < 0) continue;
      var x = u.valToPos(u.data[0][idx], "x", true);
      var y = u.valToPos(ys[idx], "y", true);
      ctx.save();
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, 2 * Math.PI);
      ctx.fillStyle = surface;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, 2 * Math.PI);
      ctx.fillStyle = color(panel.series[s - 1].color || "--chart-text");
      ctx.fill();
      ctx.restore();
    }
  }

  function drawKpBars(u) {
    var xs = u.data[0], ys = u.data[1];
    if (!xs.length) return;
    var ctx = u.ctx;
    // Narrowest spacing between two real bars, so a null bucket (a gap)
    // never makes the bars next to it look wider.
    var colWidthPx = 20;
    for (var j = 1; j < xs.length; j++) {
      var w = Math.abs(u.valToPos(xs[j], "x", true) - u.valToPos(xs[j - 1], "x", true));
      if (w > 0 && (j === 1 || w < colWidthPx)) colWidthPx = w;
    }
    var barWidth = Math.max(2, colWidthPx * 0.7);
    var baseline = u.valToPos(0, "y", true);
    ctx.save();
    for (var i = 0; i < xs.length; i++) {
      var v = ys[i];
      if (v == null) continue;
      var x = u.valToPos(xs[i], "x", true);
      var y = u.valToPos(v, "y", true);
      ctx.fillStyle = color(kpSeverityVar(v));
      ctx.fillRect(x - barWidth / 2, Math.min(y, baseline), barWidth, Math.max(1, Math.abs(baseline - y)));
    }
    ctx.restore();
  }

  function drawSourceMarkers(u, source) {
    if (!source) return;
    var ctx = u.ctx;
    ctx.save();
    ctx.strokeStyle = color("--chart-axis");
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    var prev = null;
    for (var i = 0; i < source.length; i++) {
      var s = source[i];
      if (s && prev && s !== prev) {
        var x = u.valToPos(u.data[0][i], "x", true);
        ctx.beginPath();
        ctx.moveTo(x, u.bbox.top);
        ctx.lineTo(x, u.bbox.top + u.bbox.height);
        ctx.stroke();
      }
      if (s) prev = s;
    }
    ctx.restore();
  }

  function drawExtras(panelState) {
    var panel = panelState.spec;
    return function (u) {
      if (panel.show_source) drawSourceMarkers(u, panelState.lastData && panelState.lastData.source);
      if (panel.kind === "bars") drawKpBars(u);
      else drawLastDatumDots(u, panel);
      // Drawn last so a dashed threshold stays visible crossing a bar/line,
      // not hidden underneath it.
      if (panel.zero_line) drawHLine(u, 0, color("--chart-ref"), false, null);
      (panel.ref_lines || []).forEach(function (r) {
        drawHLine(u, r.value, color("--chart-ref"), true, r.label);
      });
      // The class letters themselves sit on the right-hand axis.
      (panel.log_bands || []).forEach(function (b) {
        drawHLine(u, b.value, color("--chart-grid"), false, null);
      });
    };
  }

  // ---- building a uPlot instance ---------------------------------------

  function axisOpts(extra) {
    return Object.assign(
      {
        stroke: color("--chart-text"),
        grid: { stroke: color("--chart-grid"), width: 1 },
        ticks: { stroke: color("--chart-axis"), width: 1 },
      },
      extra || {}
    );
  }

  // Column order handed to uPlot: [time, mean_1..mean_n, (min_1, max_1)...]
  // — the band series come after every line so legend/aria indices of the
  // real series don't move.
  function columnsFor(panel, data) {
    var columns = [data.time];
    panel.series.forEach(function (s) {
      columns.push(data.series[s.field] || []);
    });
    if (panel.envelope) {
      panel.series.forEach(function (s) {
        columns.push((data.min || {})[s.field] || []);
        columns.push((data.max || {})[s.field] || []);
      });
    }
    return columns;
  }

  function buildSeries(panel, i18n) {
    var series = [{
      label: i18n.time_label,
      value: function (u, v) {
        if (v == null) return "";
        var d = new Date(v * 1000);
        return fmtDate(d, i18n) + " " + fmtTime(d) + " UT";
      },
    }];
    panel.series.forEach(function (s) {
      series.push({
        label: s.label,
        // Kp is hand-drawn as bars (drawKpBars) -- width 0 keeps uPlot from
        // also stroking a line path, but the legend still needs a swatch.
        stroke: panel.kind === "bars" ? color("--status-stale") : color(s.color),
        width: panel.kind === "bars" ? 0 : 1.5 / (window.devicePixelRatio || 1),
        points: { show: false },
        value: function (u, v) { return v == null ? "—" : fmtNum(v); },
      });
    });
    if (panel.envelope) {
      panel.series.forEach(function (s) {
        ["min", "max"].forEach(function (kind) {
          series.push({ label: s.label + " " + kind, stroke: "transparent", width: 0, points: { show: false } });
        });
      });
    }
    return series;
  }

  function buildBands(panel) {
    if (!panel.envelope) return undefined;
    var n = panel.series.length;
    return panel.series.map(function (s, i) {
      var minIdx = n + 1 + 2 * i;
      return { series: [minIdx + 1, minIdx], fill: withAlpha(color(s.color), BAND_ALPHA) };
    });
  }

  function buildOpts(panelState, width, i18n) {
    var panel = panelState.spec;
    var yAxis = axisOpts({ label: panel.unit || undefined });
    var axes = [
      axisOpts({ label: i18n.time_axis_label, values: timeAxisValues(i18n) }),
      yAxis,
    ];
    var yScale = { distr: panel.y === "log" ? 3 : 1 };
    if (panel.y_range) {
      yScale.range = function () { return panel.y_range.slice(); };
      yScale.auto = false;
    }
    if (panel.y === "log") {
      yAxis.splits = decadeSplits;
      yAxis.values = function (u, vals) {
        return vals.map(function (v) { return v == null ? "" : v.toExponential(0); });
      };
    }
    if (panel.log_bands && panel.log_bands.length) {
      // NOAA-style flare classes on the right: each letter centered in its
      // own decade (A between 1e-8 and 1e-7, ..., X above 1e-4).
      // Keyed by decade, not by the float itself: uPlot rounds the splits
      // it hands back to `values`, so an exact-float lookup misses.
      var letters = {};
      var mids = panel.log_bands.map(function (b) {
        letters[Math.round(Math.log10(b.value))] = b.label;
        return b.value * Math.sqrt(10);
      });
      axes.push(axisOpts({
        scale: "y",
        side: 1,
        size: 28,
        grid: { show: false },
        ticks: { show: false },
        splits: function () { return mids; },
        // uPlot's default log-axis filter blanks every split that isn't a
        // "nice" log value, which would drop all of these half-decades.
        filter: function (u, splits) { return splits; },
        values: function (u, vals) {
          return vals.map(function (v) { return v == null ? "" : letters[Math.floor(Math.log10(v))] || ""; });
        },
      }));
    }
    var nMain = panel.series.length;
    return {
      width: width,
      height: panelState.height,
      padding: [8, 12, 4, 4],
      tzDate: function (ts) { return uPlot.tzDate(new Date(ts * 1000), "Etc/UTC"); }, // eslint-disable-line no-undef
      series: buildSeries(panel, i18n),
      bands: buildBands(panel),
      scales: { y: yScale },
      axes: axes,
      cursor: {
        sync: { key: panelState.syncKey },
        // Band series are stroked "transparent", so their hover dots are too.
        points: { size: 8 },
      },
      legend: { show: nMain > 1 || panel.kind === "bars" },
      hooks: {
        draw: [drawExtras(panelState)],
        init: [function (u) { hideBandLegendRows(u, nMain); }],
        ready: [syncLegendIdle],
        setCursor: [syncLegendIdle],
      },
    };
  }

  function hideBandLegendRows(u, nMain) {
    var rows = u.root.querySelectorAll(".u-legend .u-series");
    // style.display, not `hidden`: uPlot's own CSS sets display on these rows.
    for (var i = nMain + 1; i < rows.length; i++) rows[i].style.display = "none";
  }

  // 13-AUDIT §3: no "Hora: --" legend while the cursor is off the chart.
  function syncLegendIdle(u) {
    u.root.classList.toggle("swx-legend-idle", u.cursor.idx == null);
  }

  // ---- panel lifecycle ---------------------------------------------------

  var liveStacks = [];

  function trendPhrase(values, i18n) {
    var idx = lastIndexWithValue(values);
    if (idx < 1) return "";
    var prevIdx = -1;
    for (var i = idx - 1; i >= 0; i--) {
      if (values[i] != null) { prevIdx = i; break; }
    }
    if (prevIdx < 0) return "";
    var delta = values[idx] - values[prevIdx];
    if (Math.abs(delta) < 1e-12) return i18n.trend_flat;
    return delta > 0 ? i18n.trend_up : i18n.trend_down;
  }

  function destroyPanel(panelState) {
    if (panelState.uplot) {
      panelState.uplot.destroy();
      panelState.uplot = null;
    }
    panelState.chartEl.innerHTML = ""; // uPlot never clears a mount point itself (v0.1's stuck "Cargando…")
  }

  function renderPanel(panelState, data, i18n, rebuild) {
    var el = panelState.chartEl;
    var statusEl = panelState.statusEl;
    var panel = panelState.spec;
    panelState.lastData = data;

    if (!data || !data.time || !data.time.length) {
      destroyPanel(panelState);
      el.classList.add("swx-panel__chart--empty");
      statusEl.hidden = false;
      statusEl.textContent = data ? i18n.no_data_message : i18n.error_message;
      el.setAttribute("aria-label", panel.title + ": " + statusEl.textContent);
      return;
    }
    el.classList.remove("swx-panel__chart--empty");
    statusEl.hidden = true;

    var columns = columnsFor(panel, data);
    if (panelState.uplot && !rebuild) {
      // Same panel, new range: swap the data in place instead of tearing
      // the chart down (16-PERF B2) — no DOM churn, no relayout.
      panelState.uplot.setData(columns);
    } else {
      destroyPanel(panelState);
      panelState.width = el.clientWidth || 600;
      panelState.uplot = new uPlot(buildOpts(panelState, panelState.width, i18n), columns, el); // eslint-disable-line no-undef
    }

    var parts = panel.series.map(function (s, i) {
      var values = columns[i + 1] || [];
      var lastIdx = lastIndexWithValue(values);
      if (lastIdx < 0) return null;
      var trend = trendPhrase(values, i18n);
      return s.label + " " + fmtNum(values[lastIdx]) + " " + panel.unit + (trend ? " (" + trend + ")" : "");
    }).filter(Boolean);
    el.setAttribute("aria-label", panel.title + ": " + (parts.length ? parts.join("; ") : i18n.no_data_message));

    if (panel.show_source && panelState.sourceEl) {
      var source = data.source || [];
      var lastSourceIdx = lastIndexWithValue(source);
      panelState.sourceEl.textContent = lastSourceIdx >= 0 ? i18n.active_source + " " + source[lastSourceIdx] : "";
    }
  }

  // `--panel-chart-height` lives on .swx-panel-stack in base.css, with a
  // media-query override for narrow screens -- read on resize, not cached,
  // so a breakpoint crossed while resizing picks up the new height too.
  function panelHeight(stackEl) {
    var v = parseInt(getComputedStyle(stackEl).getPropertyValue("--panel-chart-height"), 10);
    return v || 240;
  }

  function initStack(stackEl) {
    // B6: parsed once here, never again per panel or per theme change.
    var i18n = JSON.parse(document.getElementById(stackEl.dataset.i18nId).textContent);
    var panels = JSON.parse(document.getElementById(stackEl.dataset.panelsId).textContent);
    var panelEls = stackEl.querySelectorAll(".swx-panel");
    var syncKey = stackEl.dataset.syncKey;
    var height = panelHeight(stackEl);

    var states = panels.map(function (panel, i) {
      var root = panelEls[i];
      return {
        spec: panel,
        syncKey: syncKey,
        height: height,
        width: 0,
        chartEl: root.querySelector(".swx-panel__chart"),
        statusEl: root.querySelector(".swx-panel__status"),
        sourceEl: root.querySelector(".swx-panel__source"),
        uplot: null,
        lastData: null,
      };
    });

    stackEl.__swxPanelStates = states; // read by the CI screenshot job (.github/scripts/screenshots.py)

    var endpoints = {};
    panels.forEach(function (p) { endpoints[p.product_id] = p.endpoint; });
    var productIds = Object.keys(endpoints);

    var cache = new Map(); // range -> {data: {product_id: payload}, fetchedAt}
    var controller = null;

    function renderAll(dataById, rebuild) {
      states.forEach(function (state) {
        renderPanel(state, dataById[state.spec.product_id], i18n, rebuild);
      });
    }

    function load(range) {
      var hit = cache.get(range);
      if (hit && Date.now() - hit.fetchedAt < CACHE_TTL_MS) {
        if (controller) controller.abort();
        renderAll(hit.data, false);
        return;
      }
      if (controller) controller.abort(); // B1: a superseded click never lands
      var mine = (controller = window.AbortController ? new AbortController() : null);
      var signal = mine ? mine.signal : undefined;
      Promise.all(productIds.map(function (id) {
        return fetch(endpoints[id] + "?range=" + encodeURIComponent(range), { signal: signal })
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          })
          .then(function (data) { return [id, data]; }, function (err) {
            if (err && err.name === "AbortError") throw err;
            return [id, null];
          });
      })).then(function (results) {
        if (stackEl.dataset.range !== range) return; // B1: stale response
        var dataById = {};
        var complete = true;
        results.forEach(function (r) {
          dataById[r[0]] = r[1];
          if (r[1] == null) complete = false;
        });
        if (complete) cache.set(range, { data: dataById, fetchedAt: Date.now() });
        renderAll(dataById, false);
      }, function () { /* aborted: a newer load owns the stack now */ });
    }

    var rangeRow = document.querySelector('[data-range-row="' + syncKey + '"]');
    if (rangeRow) {
      rangeRow.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-chart-range]");
        if (!btn || btn.getAttribute("aria-disabled") === "true") return;
        if (btn.dataset.chartRange === stackEl.dataset.range) return;
        stackEl.dataset.range = btn.dataset.chartRange;
        rangeRow.querySelectorAll("[data-chart-range]").forEach(function (b) {
          b.setAttribute("aria-pressed", b === btn ? "true" : "false");
        });
        load(stackEl.dataset.range);
      });
    }

    // B4: react to width changes only (a height change is our own doing —
    // a panel switching to its empty state — and must not trigger a
    // redraw of every chart), once per animation frame at most.
    if (window.ResizeObserver) {
      var lastWidth = stackEl.clientWidth;
      var frame = 0;
      new ResizeObserver(function () {
        if (frame) return;
        frame = requestAnimationFrame(function () {
          frame = 0;
          var w = stackEl.clientWidth;
          if (w === lastWidth) return;
          lastWidth = w;
          var h = panelHeight(stackEl);
          states.forEach(function (state) {
            state.height = h;
            var cw = state.chartEl.clientWidth || 600;
            if (state.uplot && (cw !== state.width || h !== state.uplot.height)) {
              state.width = cw;
              state.uplot.setSize({ width: cw, height: h });
            }
          });
        });
      }).observe(stackEl);
    }

    var stack = {
      rebuild: function () {
        states.forEach(function (state) {
          if (state.lastData) renderPanel(state, state.lastData, i18n, true);
        });
      },
    };
    liveStacks.push(stack);
    load(stackEl.dataset.range);
  }

  function initAll() {
    resolvePalette();
    document.querySelectorAll(".swx-panel-stack").forEach(initStack);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
  document.addEventListener("swx:themechange", function () {
    resolvePalette();
    liveStacks.forEach(function (stack) { stack.rebuild(); });
  });
})();

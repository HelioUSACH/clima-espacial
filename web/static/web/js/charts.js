/* Stacked, synced-crosshair chart panels (12-HANDOFF-2026-10-15.md §4).
 * Wires vendored uPlot (web/static/web/vendor/uplot/) to the
 * `/productos/<id>/serie.json` endpoint. No build step, no CDN (D-002):
 * plain ES2017, loaded with <script defer>.
 *
 * Series colors are CSS variable *names* (e.g. "--chart-s1"), not hex: a
 * <canvas> context can't resolve var(...) the way the DOM can, so every
 * color is read with getComputedStyle at render time and again on
 * `swx:themechange` (D-053 theme contract), when every live stack is torn
 * down and rebuilt from its last-fetched data (no refetch needed).
 *
 * Kp's bars and every zero/reference/class-band line are hand-drawn in a
 * `hooks.draw` callback against uPlot's public `valToPos`/`ctx` API,
 * instead of uPlot's own (ESM-only, build-step-only) bars path plugin —
 * this project ships no bundler (D-002), so that plugin can't be vendored.
 */
(function () {
  "use strict";

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // Kept in sync with web/charts.py KP_SEVERITY_THRESHOLDS/KP_SEVERITY_STATUSES
  // (NOAA's Kp->G mapping folded onto the portal's 4-step status vocabulary).
  var KP_THRESHOLDS = [4, 5, 7];
  var KP_STATUSES = ["ok", "stale", "serious", "error"];

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
    var digits = abs >= 100 ? 0 : abs >= 1 ? 1 : 2;
    return v.toFixed(digits);
  }

  function fetchJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function lastIndexWithValue(arr) {
    for (var i = arr.length - 1; i >= 0; i--) {
      if (arr[i] != null) return i;
    }
    return -1;
  }

  // ---- hand-drawn overlays (hooks.draw) --------------------------------

  function drawHLine(u, value, color, dashed, label) {
    var top = u.valToPos(value, "y", true);
    if (!(top >= u.bbox.top && top <= u.bbox.top + u.bbox.height)) return;
    var ctx = u.ctx;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.setLineDash(dashed ? [5, 4] : []);
    ctx.beginPath();
    ctx.moveTo(u.bbox.left, top);
    ctx.lineTo(u.bbox.left + u.bbox.width, top);
    ctx.stroke();
    if (label) {
      ctx.setLineDash([]);
      ctx.fillStyle = color;
      ctx.font = "11px system-ui, sans-serif";
      ctx.textBaseline = "bottom";
      ctx.fillText(label, u.bbox.left + u.bbox.width - ctx.measureText(label).width - 4, top - 2);
    }
    ctx.restore();
  }

  function drawLastDatumDots(u, panel) {
    var ctx = u.ctx;
    var surface = cssVar("--surface-card");
    for (var s = 1; s < panel.series.length + 1; s++) {
      var ys = u.data[s];
      var idx = lastIndexWithValue(ys);
      if (idx < 0) continue;
      var x = u.valToPos(u.data[0][idx], "x", true);
      var y = u.valToPos(ys[idx], "y", true);
      var color = cssVar(panel.series[s - 1].color || "--chart-text");
      ctx.save();
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, 2 * Math.PI);
      ctx.fillStyle = surface || "#fff";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.restore();
    }
  }

  function drawKpBars(u, panel) {
    var xs = u.data[0], ys = u.data[1];
    if (!xs.length) return;
    var ctx = u.ctx;
    var colWidthPx = xs.length > 1 ? Math.abs(u.valToPos(xs[1], "x", true) - u.valToPos(xs[0], "x", true)) : 20;
    var barWidth = Math.max(2, colWidthPx * 0.7);
    var baseline = u.valToPos(0, "y", true);
    ctx.save();
    for (var i = 0; i < xs.length; i++) {
      var v = ys[i];
      if (v == null) continue;
      var x = u.valToPos(xs[i], "x", true);
      var y = u.valToPos(v, "y", true);
      var top = Math.min(y, baseline);
      var height = Math.max(1, Math.abs(baseline - y));
      ctx.fillStyle = cssVar(kpSeverityVar(v));
      ctx.fillRect(x - barWidth / 2, top, barWidth, height);
    }
    ctx.restore();
  }

  function drawSourceMarkers(u, source) {
    if (!source) return;
    var ctx = u.ctx;
    var color = cssVar("--chart-axis");
    ctx.save();
    ctx.strokeStyle = color;
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

  function drawExtras(panel, source) {
    return function (u) {
      if (panel.show_source) drawSourceMarkers(u, source);
      if (panel.kind === "bars") drawKpBars(u, panel);
      else drawLastDatumDots(u, panel);
      // Drawn last so a dashed threshold stays visible crossing a bar/line,
      // not hidden underneath it.
      if (panel.zero_line) drawHLine(u, 0, cssVar("--chart-ref"), false, null);
      (panel.ref_lines || []).forEach(function (r) {
        drawHLine(u, r.value, cssVar("--chart-ref"), true, r.label);
      });
      (panel.log_bands || []).forEach(function (b) {
        drawHLine(u, b.value, cssVar("--chart-grid"), false, b.label);
      });
    };
  }

  // ---- building a uPlot instance ---------------------------------------

  function axisOpts(extra) {
    return Object.assign(
      {
        stroke: cssVar("--chart-text"),
        grid: { stroke: cssVar("--chart-grid"), width: 1 },
        ticks: { stroke: cssVar("--chart-axis"), width: 1 },
      },
      extra || {}
    );
  }

  function buildSeries(panel, timeLabel) {
    var series = [{ label: timeLabel }];
    panel.series.forEach(function (s) {
      series.push({
        label: s.label,
        // Kp is hand-drawn as bars (drawKpBars) -- width 0 keeps uPlot from
        // also stroking a line path, but the legend still needs a swatch.
        stroke: panel.kind === "bars" ? cssVar("--status-stale") : cssVar(s.color),
        width: panel.kind === "bars" ? 0 : 1.5 / (window.devicePixelRatio || 1),
        points: { show: false },
      });
    });
    return series;
  }

  function buildOpts(panel, width, height, syncKey, timeLabel) {
    var yAxis = axisOpts({ label: panel.unit || undefined });
    if (panel.y === "log") {
      yAxis.values = function (u, vals) {
        return vals.map(function (v) {
          return v == null ? "" : v.toExponential(0);
        });
      };
    }
    return {
      width: width,
      height: height,
      padding: [8, 12, 4, 4],
      series: buildSeries(panel, timeLabel),
      scales: { y: { distr: panel.y === "log" ? 3 : 1 } },
      axes: [axisOpts({}), yAxis],
      cursor: { sync: { key: syncKey }, points: { size: 8 } },
      legend: { show: panel.series.length > 1 || panel.kind === "bars" },
      hooks: { draw: [] }, // filled in per-instance in renderPanel, see drawExtras
    };
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
    if (Math.abs(delta) < 1e-9) return i18n.trend_flat;
    return delta > 0 ? i18n.trend_up : i18n.trend_down;
  }

  function renderEmpty(statusEl, message) {
    statusEl.textContent = message;
    statusEl.hidden = false;
  }

  function renderPanel(panelState, data, i18n) {
    var el = panelState.chartEl;
    var statusEl = panelState.statusEl;
    var panel = panelState.spec;

    if (panelState.uplot) {
      panelState.uplot.destroy();
      panelState.uplot = null;
    }
    el.innerHTML = ""; // uPlot never clears a mount point itself (v0.1's stuck "Cargando…")

    if (!data || !data.time || !data.time.length) {
      el.classList.add("swx-panel__chart--empty");
      statusEl.hidden = false;
      statusEl.textContent = data ? i18n.no_data_message : i18n.error_message;
      el.setAttribute("aria-label", panel.title + ": " + statusEl.textContent);
      return;
    }
    el.classList.remove("swx-panel__chart--empty");
    statusEl.hidden = true;

    var columns = [data.time];
    panel.series.forEach(function (s) {
      columns.push(data.series[s.field] || []);
    });

    var opts = buildOpts(panel, el.clientWidth || 600, panelState.height, panelState.syncKey, i18n.time_label);
    opts.hooks.draw.push(drawExtras(panel, data.source));
    var u = new uPlot(opts, columns, el); // eslint-disable-line no-undef
    panelState.uplot = u;
    panelState.lastData = data;

    var parts = panel.series.map(function (s, i) {
      var values = columns[i + 1] || [];
      var lastIdx = lastIndexWithValue(values);
      if (lastIdx < 0) return null;
      var trend = trendPhrase(values, i18n);
      return s.label + " " + fmtNum(values[lastIdx]) + " " + panel.unit + (trend ? " (" + trend + ")" : "");
    }).filter(Boolean);
    el.setAttribute("aria-label", panel.title + ": " + (parts.length ? parts.join("; ") : i18n.no_data_message));

    if (panel.show_source && panelState.sourceEl) {
      var lastSourceIdx = lastIndexWithValue(data.source || []);
      panelState.sourceEl.textContent =
        lastSourceIdx >= 0 ? i18n.active_source + " " + data.source[lastSourceIdx] : "";
    }
  }

  // `--panel-chart-height` lives on .swx-panel-stack in base.css, with a
  // media-query override for narrow screens -- read fresh (not cached) so
  // a breakpoint crossed while resizing picks up the new height too.
  function panelHeight(stackEl) {
    var v = parseInt(getComputedStyle(stackEl).getPropertyValue("--panel-chart-height"), 10);
    return v || 240;
  }

  function initStack(stackEl) {
    var i18n = JSON.parse(document.getElementById(stackEl.dataset.i18nId).textContent);
    var panels = JSON.parse(document.getElementById(stackEl.dataset.panelsId).textContent);
    var panelEls = stackEl.querySelectorAll(".swx-panel");
    var syncKey = stackEl.dataset.syncKey;

    var states = panels.map(function (panel, i) {
      var root = panelEls[i];
      return {
        spec: panel,
        syncKey: syncKey,
        height: panelHeight(stackEl),
        chartEl: root.querySelector(".swx-panel__chart"),
        statusEl: root.querySelector(".swx-panel__status"),
        sourceEl: root.querySelector(".swx-panel__source"),
        uplot: null,
        lastData: null,
      };
    });
    stackEl.__swxPanelStates = states;
    liveStacks.push(stackEl);

    function load(range) {
      var byProduct = {};
      panels.forEach(function (p) {
        byProduct[p.product_id] = p.endpoint;
      });
      var ids = Object.keys(byProduct);
      Promise.all(
        ids.map(function (id) {
          return fetchJSON(byProduct[id] + "?range=" + encodeURIComponent(range))
            .then(function (data) { return [id, data]; })
            .catch(function () { return [id, null]; });
        })
      ).then(function (results) {
        var dataById = {};
        results.forEach(function (r) { dataById[r[0]] = r[1]; });
        states.forEach(function (state) {
          renderPanel(state, dataById[state.spec.product_id], i18n);
        });
      });
    }

    load(stackEl.dataset.range);

    var rangeRow = document.querySelector('[data-range-row="' + stackEl.dataset.syncKey + '"]');
    if (rangeRow) {
      rangeRow.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-chart-range]");
        if (!btn) return;
        stackEl.dataset.range = btn.dataset.chartRange;
        rangeRow.querySelectorAll("[data-chart-range]").forEach(function (b) {
          b.setAttribute("aria-pressed", b === btn ? "true" : "false");
        });
        load(stackEl.dataset.range);
      });
    }

    if (window.ResizeObserver) {
      var ro = new ResizeObserver(function () {
        var h = panelHeight(stackEl);
        states.forEach(function (state) {
          state.height = h;
          if (state.uplot) state.uplot.setSize({ width: state.chartEl.clientWidth || 600, height: h });
        });
      });
      ro.observe(stackEl);
    }
  }

  function rebuildOnThemeChange() {
    liveStacks.forEach(function (stackEl) {
      (stackEl.__swxPanelStates || []).forEach(function (state) {
        if (state.lastData) {
          var i18n = JSON.parse(document.getElementById(stackEl.dataset.i18nId).textContent);
          renderPanel(state, state.lastData, i18n);
        }
      });
    });
  }

  function initAll() {
    document.querySelectorAll(".swx-panel-stack").forEach(initStack);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
  document.addEventListener("swx:themechange", rebuildOnThemeChange);
})();

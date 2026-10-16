/* One ECharts instance per panel stack, N touching grids, a synced
 * axisPointer and inside dataZoom across all of them (18-HANDOFF-v0.2.0.md
 * §1; reference implementation: ref/mockup-v0.2/echarts-stack-prototype.html
 * section A). Wires vendored ECharts (web/static/web/vendor/echarts/) to
 * the bucketed `/productos/<id>/serie.json` endpoint (D-056/D-057). No
 * build step, no CDN (D-002): plain ES2017, loaded with <script defer>.
 *
 * A stack's panels can span more than one product (home: noaa.rtsw.mag +
 * noaa.rtsw.wind in one 5-grid instance) — `load()` below still fetches
 * each distinct product once and keys the result by product_id, same as
 * v0.1.3's per-panel-chart version did.
 *
 * Every v0.1.3 behaviour (16-PERF-AND-CHART-ENGINE.md §2, D-058) survives
 * the engine swap: palette resolved once per render (B5), AbortController +
 * stale-response guard (B1), a 60 s per-range client cache reusing setOption
 * instead of a rebuild (B2), width-only resize (B4), i18n parsed once (B6).
 */
(function () {
  "use strict";

  var CACHE_TTL_MS = 60 * 1000; // matches web/charts.py SERIES_CACHE_SECONDS
  var BAND_ALPHA = 0.16;
  var SOURCE_MARK_MERGE_SECONDS = 30 * 60; // a run shorter than this merges into one marker

  // Kept in sync with web/charts.py KP_SEVERITY_THRESHOLDS/KP_SEVERITY_STATUSES.
  var KP_THRESHOLDS = [5, 6, 7, 8];
  var KP_STATUSES = ["ok", "minor", "moderate", "strong", "severe"];

  function kpSeverityVar(value) {
    if (value == null) return null;
    for (var i = 0; i < KP_THRESHOLDS.length; i++) {
      if (value < KP_THRESHOLDS[i]) return "--severity-" + KP_STATUSES[i];
    }
    return "--severity-" + KP_STATUSES[KP_STATUSES.length - 1];
  }

  // ---- palette (B5): resolved once per render, read many times ----------

  var PALETTE_VARS = [
    "--ink", "--ink-soft", "--ink-faint", "--line", "--grid", "--raised", "--accent", "--bt", "--ref",
    "--chart-s1", "--chart-s2", "--chart-s3", "--chart-s4", "--chart-s5", "--chart-s6",
    "--severity-ok", "--severity-minor", "--severity-moderate", "--severity-strong", "--severity-severe",
  ];

  function readPalette() {
    var style = getComputedStyle(document.documentElement);
    var out = {};
    PALETTE_VARS.forEach(function (name) { out[name] = style.getPropertyValue(name).trim(); });
    return out;
  }

  // "#rrggbb" / "#rgb" -> "rgba(r,g,b,a)"; anything else (already rgba(),
  // or a named colour) is returned as-is rather than guessed at.
  function withAlpha(c, alpha) {
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c);
    if (!m) return c;
    var hex = m[1].length === 3 ? m[1].replace(/./g, "$&$&") : m[1];
    var n = parseInt(hex, 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
  }

  function fmtNum(v) {
    if (v == null) return "—";
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

  function pad2(n) { return n < 10 ? "0" + n : "" + n; }

  function fmtDate(d, i18n) {
    if (i18n.lang === "en") return pad2(d.getUTCDate()) + " " + i18n.months[d.getUTCMonth()];
    return pad2(d.getUTCDate()) + "/" + pad2(d.getUTCMonth() + 1);
  }
  function fmtTime(d) { return pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes()); }
  function fmtUT(sec, i18n) {
    var d = new Date(sec * 1000);
    return fmtDate(d, i18n) + " " + fmtTime(d) + " UT";
  }

  // ---- geometry: fixed per-grid pixel height, from the approved mockup --

  function geom(tall) {
    var narrow = window.innerWidth < 680;
    var H = narrow ? (tall ? 150 : 92) : (tall ? 210 : 118);
    return { H: H, top: 8, bottom: 30, left: narrow ? 44 : 58, right: 8 };
  }

  // ---- building the ECharts option -------------------------------------

  function buildMarks(sources) {
    // sources: array of [productId sourceArray aligned to its own time
    // array]; we only have the stack's shared x grid here via `times`, so
    // the caller passes pre-resolved [timestamp, label] change points.
    var marks = [];
    for (var i = 1; i < sources.length; i++) {
      var next = sources[i + 1], dur = next ? next[0] - sources[i][0] : Infinity;
      if (dur < SOURCE_MARK_MERGE_SECONDS) {
        marks.push([sources[i][0], sources[i][1] + " · " + Math.max(1, Math.round(dur / 60)) + " min"]);
        i++;
      } else {
        marks.push([sources[i][0], sources[i][1]]);
      }
    }
    return marks;
  }

  function sourceChangePoints(times, source) {
    var out = [];
    var prev = null;
    for (var i = 0; i < source.length; i++) {
      if (source[i] && prev && source[i] !== prev) out.push([times[i], source[i]]);
      if (source[i]) prev = source[i];
    }
    return out;
  }

  function seriesColor(slotName, P) {
    return P[slotName] || P["--ink-faint"];
  }

  function buildStackOption(stack, dataById, P) {
    var panels = stack.panels, g = geom(stack.tall);
    var grids = [], xs = [], ys = [], series = [], titles = [];
    var MONO = "JetBrains Mono, ui-monospace, monospace";
    var UI = "Poppins, system-ui, sans-serif";

    // A shared time domain across every grid: the widest span any panel's
    // product actually returned (a product with no data yet still shares
    // the others' axis instead of collapsing the stack).
    var tmin = null, tmax = null;
    panels.forEach(function (p) {
      var d = dataById[p.product_id];
      if (d && d.time && d.time.length) {
        var t0 = d.time[0] * 1000, t1 = d.time[d.time.length - 1] * 1000;
        tmin = tmin == null ? t0 : Math.min(tmin, t0);
        tmax = tmax == null ? t1 : Math.max(tmax, t1);
      }
    });
    if (tmin == null) { tmin = Date.now() - 86400000; tmax = Date.now(); }
    stack.spanSeconds = (tmax - tmin) / 1000; // read by .github/scripts/screenshots.py

    var allSourceChanges = [];
    var panelPrimary = []; // the one real series per panel that hosts its markLine/markArea
    panels.forEach(function (panel, pi) {
      var last = pi === panels.length - 1;
      var data = dataById[panel.product_id];
      var times = (data && data.time) || [];

      grids.push({
        left: g.left, right: g.right, top: g.top + pi * g.H, height: g.H,
        show: true, borderColor: P["--line"], borderWidth: 1, backgroundColor: "transparent",
      });
      xs.push({
        type: "time", gridIndex: pi, min: tmin, max: tmax,
        axisLine: { show: false }, axisTick: { show: last, lineStyle: { color: P["--line"] } },
        axisLabel: {
          show: last, color: P["--ink-faint"], fontFamily: MONO, fontSize: 10, hideOverlap: true,
          formatter: function (v) {
            var d = new Date(v);
            return (d.getUTCHours() === 0 && d.getUTCMinutes() === 0) ? "{d|" + fmtDate(d, stack.i18n) + "}" : fmtTime(d);
          },
          rich: { d: { fontWeight: 600, color: P["--ink-soft"], fontFamily: MONO, fontSize: 10 } },
        },
        splitLine: { show: true, lineStyle: { color: P["--grid"] } },
        name: last ? stack.i18n.time_axis_label : undefined,
        nameLocation: "end", nameGap: 4,
        nameTextStyle: { color: P["--ink-faint"], fontFamily: MONO, fontSize: 9 },
      });

      var isLog = panel.y === "log";
      var yAxis = {
        type: isLog ? "log" : "value", gridIndex: pi, scale: !panel.zero_line, splitNumber: 3,
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: {
          showMinLabel: false, showMaxLabel: false, color: P["--ink-faint"], fontFamily: MONO, fontSize: 10,
          formatter: isLog ? function (v) { return v.toExponential(0); } : null,
        },
        splitLine: { show: true, lineStyle: { color: P["--grid"] } },
      };
      if (panel.y_range) { yAxis.min = panel.y_range[0]; yAxis.max = panel.y_range[1]; }
      else if (!isLog) {
        yAxis.min = function (v) { return Math.floor(v.min - (v.max - v.min) * 0.08); };
        yAxis.max = function (v) { return Math.ceil(v.max + (v.max - v.min) * 0.08); };
      }
      ys.push(yAxis);

      // A `title` component (not `graphic`: GraphicComponent isn't in the
      // trimmed build, ref/echarts-swx/entry.js) positioned over its own
      // grid -- ECharts supports an array of independent titles.
      titles.push({
        text: panel.title + "  ·  " + panel.unit, left: g.left + 8, top: g.top + pi * g.H + 5,
        textStyle: { fontFamily: UI, fontSize: 10, fontWeight: 500, color: P["--ink-faint"] },
      });

      var ml = [];
      if (panel.zero_line) ml.push({ yAxis: 0, lineStyle: { color: P["--ref"], width: 1, type: "solid" }, label: { show: false } });
      (panel.ref_lines || []).forEach(function (r) {
        ml.push({
          yAxis: r.value, lineStyle: { color: P["--ref"], width: 1, type: "dashed" },
          label: { show: true, formatter: r.label, color: P["--ink-faint"], fontFamily: MONO, fontSize: 9, position: "insideEndTop" },
        });
      });
      (panel.log_bands || []).forEach(function (b) {
        // Centred in its own decade (geometric mean of the two edges), with
        // no visible line — the markArea below already shows the band.
        ml.push({
          yAxis: b.value * Math.sqrt(10), lineStyle: { opacity: 0 },
          label: { show: true, formatter: b.label, color: P["--ink-faint"], fontFamily: MONO, fontSize: 9, position: "end" },
        });
      });

      // markLine/markArea ride on a *real* series (never a phantom
      // empty-data one): ECharts' canvas compositor can hand getDataURL a
      // zero-sized layer for an empty series, which throws "the image
      // argument is a canvas element with a width or height of 0" on
      // export — caught interactively (headless Chromium), not just
      // reasoned about.
      var primary;
      if (panel.kind === "bars") {
        var values = (data && data.series[panel.series[0].field]) || [];
        primary = {
          id: panel.key, name: panel.series[0].label, type: "bar", xAxisIndex: pi, yAxisIndex: pi,
          barMaxWidth: 10, large: true, silent: true, z: 3,
          data: times.map(function (t, i) {
            var v = values[i];
            return { value: [t * 1000, v], itemStyle: v == null ? undefined : { color: P[kpSeverityVar(v)] } };
          }),
        };
        series.push(primary);
      } else {
        panel.series.forEach(function (s, si) {
          var color = seriesColor(s.color, P);
          var values = (data && data.series[s.field]) || [];
          if (panel.envelope) {
            var lo = (data && data.min[s.field]) || [], hi = (data && data.max[s.field]) || [];
            series.push({
              id: "band-lo-" + panel.key + "-" + si, type: "line", xAxisIndex: pi, yAxisIndex: pi,
              stack: "band" + panel.key + si, symbol: "none", silent: true, lineStyle: { opacity: 0 },
              data: times.map(function (t, i) { return [t * 1000, lo[i]]; }), z: 1,
            });
            series.push({
              id: "band-hi-" + panel.key + "-" + si, type: "line", xAxisIndex: pi, yAxisIndex: pi,
              stack: "band" + panel.key + si, symbol: "none", silent: true, lineStyle: { opacity: 0 },
              areaStyle: { color: withAlpha(color, BAND_ALPHA) },
              data: times.map(function (t, i) {
                return [t * 1000, (hi[i] == null || lo[i] == null) ? null : hi[i] - lo[i]];
              }), z: 1,
            });
          }
          var lineSeries = {
            id: panel.key + "-" + si, name: s.label, type: "line", xAxisIndex: pi, yAxisIndex: pi,
            showSymbol: false, symbol: "none", sampling: "none", connectNulls: false,
            lineStyle: { width: 1.5, color: color }, itemStyle: { color: color }, z: 3, emphasis: { disabled: true },
            data: times.map(function (t, i) { return [t * 1000, values[i]]; }),
          };
          series.push(lineSeries);
          if (si === 0) primary = lineSeries;
        });
        if (panel.log_bands && panel.log_bands.length) {
          // Faint class-severity bands between consecutive thresholds
          // (A=ok .. X=severe), the same shared scale as the strip chips.
          var edges = [panel.y_range ? panel.y_range[0] : 1e-9].concat(panel.log_bands.map(function (b) { return b.value; }));
          var sevOrder = ["ok", "minor", "moderate", "strong", "severe"];
          var areas = [];
          for (var bi = 0; bi < edges.length - 1 && bi < sevOrder.length; bi++) {
            var sevColor = P["--severity-" + sevOrder[bi]];
            areas.push([{ yAxis: edges[bi], itemStyle: { color: withAlpha(sevColor, 0.08) } }, { yAxis: edges[bi + 1] }]);
          }
          primary.markArea = { silent: true, data: areas };
        }
      }
      if (ml.length) primary.markLine = { silent: true, symbol: "none", animation: false, data: ml.slice() };
      panelPrimary.push(primary);

      if (panel.show_source && data && data.source) {
        sourceChangePoints(times, data.source).forEach(function (c) { allSourceChanges.push(c); });
      }
    });

    // Active-spacecraft change markers (§1): one dashed vertical line per
    // change, across every grid, label on the top grid only. Merged onto
    // each panel's existing markLine (its primary series) rather than a
    // separate phantom series, same reasoning as above.
    allSourceChanges.sort(function (a, b) { return a[0] - b[0]; });
    var marks = buildMarks([[null, null]].concat(allSourceChanges)); // mimic prototype's 1-based scan
    if (marks.length) {
      panelPrimary.forEach(function (primarySeries, pi) {
        var markData = marks.map(function (m) {
          return {
            xAxis: m[0] * 1000, lineStyle: { color: P["--ref"], width: 1, type: "dashed" },
            label: { show: pi === 0, formatter: m[1], color: P["--ink-faint"], fontFamily: MONO, fontSize: 9, position: "insideEndTop" },
          };
        });
        if (primarySeries.markLine) primarySeries.markLine.data = primarySeries.markLine.data.concat(markData);
        else primarySeries.markLine = { silent: true, symbol: "none", animation: false, data: markData };
      });
    }

    return {
      useUTC: true, animation: false, textStyle: { fontFamily: MONO },
      title: titles, grid: grids, xAxis: xs, yAxis: ys, series: series,
      axisPointer: { link: [{ xAxisIndex: "all" }], lineStyle: { color: P["--ref"], type: "dashed" }, label: { show: false } },
      tooltip: { trigger: "axis", showContent: false, axisPointer: { type: "line" } },
      dataZoom: [{ type: "inside", xAxisIndex: panels.map(function (_x, i) { return i; }), filterMode: "none" }],
    };
  }

  // ---- readout column ---------------------------------------------------

  function buildReadout(readEl, panels, H) {
    var html = "";
    panels.forEach(function (p) {
      html += '<div class="rrow" style="height:' + H + 'px">';
      p.series.forEach(function (s) {
        html += '<div><span class="k"><span class="sw" data-sw="' + s.color + '"></span>' + escapeHtml(s.label) + '</span><b data-k="' + p.key + ":" + s.field + '">—</b></div>';
      });
      html += '<div><span class="unit">' + escapeHtml(p.unit) + "</span></div></div>";
    });
    readEl.innerHTML = html;
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  function paintReadoutSwatches(readEl, P) {
    readEl.querySelectorAll("[data-sw]").forEach(function (el) {
      el.style.background = seriesColor(el.dataset.sw, P);
    });
  }

  function trendPhrase(values, i18n) {
    var idx = lastIndexWithValue(values);
    if (idx < 1) return "";
    var prevIdx = -1;
    for (var i = idx - 1; i >= 0; i--) { if (values[i] != null) { prevIdx = i; break; } }
    if (prevIdx < 0) return "";
    var delta = values[idx] - values[prevIdx];
    if (Math.abs(delta) < 1e-12) return i18n.trend_flat;
    return delta > 0 ? i18n.trend_up : i18n.trend_down;
  }

  // ---- per-stack lifecycle ------------------------------------------------

  var liveStacks = [];

  function Stack(root) {
    this.root = root;
    this.i18n = JSON.parse(document.getElementById(root.dataset.i18nId).textContent);
    this.panels = JSON.parse(document.getElementById(root.dataset.panelsId).textContent);
    this.tall = root.dataset.tall === "1";
    this.ariaLabel = root.dataset.ariaLabel || "";
    this.plotEl = root.querySelector("[data-stack-plot]");
    this.readEl = root.querySelector("[data-stack-read]");
    this.metaEl = root.querySelector("[data-stack-meta]");
    this.snapEl = root.querySelector("[data-stack-snap]");
    this.ec = null;
    this.lastDataById = null;
    this.cache = new Map(); // range -> {data, fetchedAt}
    this.controller = null;
    this.endpoints = {};
    var self = this;
    this.panels.forEach(function (p) { self.endpoints[p.product_id] = p.endpoint; });
    this.statusEls = [];
    root.__swxStack = this; // read by .github/scripts/screenshots.py
  }

  Stack.prototype.ensureStatusOverlays = function () {
    if (this.statusEls.length) return;
    var self = this;
    this.panels.forEach(function () {
      var el = document.createElement("p");
      el.className = "swx-stack-status";
      el.hidden = true;
      self.plotEl.appendChild(el);
      self.statusEls.push(el);
    });
  };

  Stack.prototype.layoutStatusOverlays = function () {
    var g = geom(this.tall);
    this.statusEls.forEach(function (el, pi) {
      el.style.top = (g.top + pi * g.H) + "px";
      el.style.height = g.H + "px";
    });
  };

  Stack.prototype.render = function (dataById, rebuild) {
    this.lastDataById = dataById;
    this.ensureStatusOverlays();
    this.layoutStatusOverlays();
    var P = readPalette();

    var anyData = false;
    var self = this;
    this.panels.forEach(function (p, pi) {
      var d = dataById[p.product_id];
      var ok = !!(d && d.time && d.time.length);
      if (ok) anyData = true;
      self.statusEls[pi].hidden = ok;
      self.statusEls[pi].textContent = ok ? "" : (d ? self.i18n.no_data_message : self.i18n.error_message);
    });

    // The container's height must be set *before* echarts.init() reads it
    // to size its internal canvas -- .plot has no CSS height of its own,
    // so init-before-resize created a real 0-height canvas (getDataURL
    // then threw drawImage-ing it; caught interactively, headless
    // Chromium, not just reasoned about).
    var g = geom(this.tall);
    this.plotEl.style.height = (g.top + this.panels.length * g.H + g.bottom) + "px";
    if (!this.ec) {
      this.ec = echarts.init(this.plotEl, null, { renderer: "canvas" }); // eslint-disable-line no-undef
    }
    var option = buildStackOption(this, dataById, P);
    this.ec.setOption(option, { notMerge: true, lazyUpdate: !rebuild });
    this.plotEl.dataset.echartsReady = "true";

    if (!this.readBuilt) { buildReadout(this.readEl, this.panels, g.H); this.readBuilt = true; }
    paintReadoutSwatches(this.readEl, P);
    this.setReadoutLatest();
    this.updateMetaAndAria(dataById, anyData);

    var self2 = this;
    this.ec.off("updateAxisPointer");
    this.ec.on("updateAxisPointer", function (e) {
      var info = e.axesInfo && e.axesInfo[0];
      if (!info) { self2.setReadoutLatest(); return; }
      self2.setReadoutAt(Math.round(info.value / 1000));
    });
    this.ec.getZr().off("globalout");
    this.ec.getZr().on("globalout", function () { self2.setReadoutLatest(); });
  };

  Stack.prototype.timeArrayFor = function (productId) {
    var d = this.lastDataById && this.lastDataById[productId];
    return (d && d.time) || [];
  };

  Stack.prototype.setReadoutAt = function (tsSec) {
    var self = this;
    this.panels.forEach(function (p) {
      var times = self.timeArrayFor(p.product_id);
      var idx = -1, best = Infinity;
      for (var i = 0; i < times.length; i++) {
        var diff = Math.abs(times[i] - tsSec);
        if (diff < best) { best = diff; idx = i; }
      }
      p.series.forEach(function (s) {
        var b = self.readEl.querySelector('[data-k="' + p.key + ":" + s.field + '"]');
        if (!b) return;
        var d = self.lastDataById && self.lastDataById[p.product_id];
        var v = idx >= 0 && d ? d.series[s.field][idx] : null;
        b.textContent = fmtNum(v);
      });
    });
  };

  Stack.prototype.setReadoutLatest = function () {
    var self = this;
    this.panels.forEach(function (p) {
      var d = self.lastDataById && self.lastDataById[p.product_id];
      p.series.forEach(function (s) {
        var b = self.readEl.querySelector('[data-k="' + p.key + ":" + s.field + '"]');
        if (!b) return;
        var values = (d && d.series[s.field]) || [];
        var idx = lastIndexWithValue(values);
        b.textContent = idx >= 0 ? fmtNum(values[idx]) : "—";
      });
    });
  };

  function fmtBucket(seconds, i18n) {
    var mins = Math.round(seconds / 60);
    var n = mins < 1 ? Math.round(seconds) : mins;
    var unit = mins < 1 ? i18n.seconds_unit : i18n.minutes_unit;
    return i18n.averages_label + " " + n + " " + unit;
  }

  Stack.prototype.updateMetaAndAria = function (dataById, anyData) {
    var self = this;
    var latestTs = null, activeSource = null, bucketSeconds = null, parts = [];
    this.panels.forEach(function (p) {
      var d = dataById[p.product_id];
      if (!d || !d.time || !d.time.length) return;
      var lastT = d.time[d.time.length - 1];
      if (latestTs == null || lastT > latestTs) latestTs = lastT;
      if (bucketSeconds == null) bucketSeconds = d.bucket_seconds;
      if (p.show_source && d.source) {
        var idx = lastIndexWithValue(d.source);
        if (idx >= 0) activeSource = d.source[idx];
      }
      p.series.forEach(function (s) {
        var values = d.series[s.field] || [];
        var idx = lastIndexWithValue(values);
        if (idx < 0) return;
        var trend = trendPhrase(values, self.i18n);
        parts.push(s.label + " " + fmtNum(values[idx]) + " " + p.unit + (trend ? " (" + trend + ")" : ""));
      });
    });
    if (this.metaEl) {
      var bits = [];
      if (activeSource) bits.push(this.i18n.active_source_label + " " + activeSource);
      if (latestTs != null) bits.push(this.i18n.last_datum_label + " " + fmtUT(latestTs, this.i18n));
      if (bucketSeconds) bits.push(fmtBucket(bucketSeconds, this.i18n));
      this.metaEl.innerHTML = bits.map(escapeHtml).join(" · ");
    }
    if (this.snapEl) {
      var hasSrcMark = this.panels.some(function (p) { return p.show_source; });
      if (hasSrcMark && latestTs != null) {
        this.snapEl.hidden = false;
        this.snapEl.innerHTML =
          "<span>" + escapeHtml(this.i18n.last_datum_label + " " + fmtUT(latestTs, this.i18n)) + "</span>" +
          "<span>" + escapeHtml(this.i18n.source_mark_note) + "</span>";
      } else {
        this.snapEl.hidden = true;
      }
    }
    this.plotEl.setAttribute(
      "aria-label",
      this.ariaLabel + (parts.length ? ": " + parts.join("; ") : ": " + (anyData ? this.i18n.no_data_message : this.i18n.error_message))
    );
  };

  Stack.prototype.load = function (range) {
    var self = this;
    var hit = this.cache.get(range);
    if (hit && Date.now() - hit.fetchedAt < CACHE_TTL_MS) {
      if (this.controller) this.controller.abort();
      this.render(hit.data, false);
      return;
    }
    if (this.controller) this.controller.abort(); // B1: a superseded click never lands
    var mine = (this.controller = window.AbortController ? new AbortController() : null);
    var signal = mine ? mine.signal : undefined;
    var ids = Object.keys(this.endpoints);
    Promise.all(ids.map(function (id) {
      return fetch(self.endpoints[id] + "?range=" + encodeURIComponent(range), { signal: signal })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) { return [id, data]; }, function (err) {
          if (err && err.name === "AbortError") throw err;
          return [id, null];
        });
    })).then(function (results) {
      if (self.root.dataset.range !== range) return; // B1: stale response
      var dataById = {};
      var complete = true;
      results.forEach(function (r) { dataById[r[0]] = r[1]; if (r[1] == null) complete = false; });
      if (complete) self.cache.set(range, { data: dataById, fetchedAt: Date.now() });
      self.render(dataById, false);
    }, function () { /* aborted: a newer load owns the stack now */ });
  };

  Stack.prototype.rebuild = function () {
    if (this.lastDataById) this.render(this.lastDataById, true);
  };

  Stack.prototype.resize = function () {
    if (!this.ec) return;
    var g = geom(this.tall);
    this.plotEl.style.height = (g.top + this.panels.length * g.H + g.bottom) + "px";
    this.layoutStatusOverlays();
    this.ec.resize();
    if (this.readBuilt) {
      this.readEl.querySelectorAll(".rrow").forEach(function (row) { row.style.height = g.H + "px"; });
    }
  };

  function wireControls(stack) {
    var root = stack.root;
    var rangeRow = document.querySelector('[data-range-row="' + root.dataset.syncKey + '"]');
    if (rangeRow) {
      rangeRow.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-chart-range]");
        if (!btn || btn.getAttribute("aria-disabled") === "true") return;
        if (btn.dataset.chartRange === root.dataset.range) return;
        root.dataset.range = btn.dataset.chartRange;
        rangeRow.querySelectorAll("[data-chart-range]").forEach(function (b) {
          var on = b === btn;
          b.setAttribute("aria-pressed", on ? "true" : "false");
          b.classList.toggle("on", on);
        });
        stack.load(root.dataset.range);
      });
    }
    var resetBtn = root.querySelector("[data-reset-zoom]");
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        if (stack.ec) stack.ec.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
      });
    }
    var pngBtn = root.querySelector("[data-png-export]");
    if (pngBtn) {
      pngBtn.addEventListener("click", function () {
        if (!stack.ec) return;
        var P = readPalette();
        var url = stack.ec.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: P["--raised"] });
        var a = document.createElement("a");
        a.href = url;
        a.download = (root.dataset.syncKey || "chart") + ".png";
        document.body.appendChild(a);
        a.click();
        a.remove();
      });
    }
  }

  function initStack(root) {
    var stack = new Stack(root);
    wireControls(stack);
    liveStacks.push(stack);

    if (window.ResizeObserver) {
      var lastWidth = root.clientWidth, frame = 0;
      new ResizeObserver(function () {
        if (frame) return;
        frame = requestAnimationFrame(function () {
          frame = 0;
          var w = root.clientWidth;
          if (w === lastWidth) return;
          lastWidth = w;
          stack.resize();
        });
      }).observe(root);
    }

    stack.load(root.dataset.range);
  }

  function initAll() {
    document.querySelectorAll(".swx-stack").forEach(initStack);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
  document.addEventListener("swx:themechange", function () {
    liveStacks.forEach(function (stack) {
      if (stack.ec) { stack.ec.dispose(); stack.ec = null; }
      stack.readBuilt = false;
      stack.rebuild();
    });
  });
})();

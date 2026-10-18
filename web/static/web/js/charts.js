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

  // ---- number formatting (19-HANDOFF-v0.2.1.md §1.4: ES comma/EN point,
  // a true minus "−" not a hyphen, grouped temperatures, no raw "1.07e+5"
  // anywhere in the UI) ----------------------------------------------------

  var SUPERSCRIPT = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻" };

  function superscript(n) {
    return String(n).split("").map(function (c) { return SUPERSCRIPT[c] !== undefined ? SUPERSCRIPT[c] : c; }).join("");
  }

  // Intl.NumberFormat renders the ASCII hyphen-minus for negatives in this
  // build's Chromium, not U+2212 (confirmed interactively) -- substituted
  // by hand since the leading sign is always the formatted string's first
  // character in these locales.
  function trueMinus(s) { return s.charAt(0) === "-" ? "−" + s.slice(1) : s; }

  function intl(lang, opts) {
    return new Intl.NumberFormat(lang === "en" ? "en-US" : "es-CL", opts);
  }

  function fmtDecimal(v, lang, digits) {
    return trueMinus(intl(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v));
  }

  // Grouped, no decimals -- a temperature in K ("107 000" / "107,000"), not
  // "1.07e+5" or an ungrouped "107000".
  function fmtGrouped(v, lang) {
    return trueMinus(intl(lang, { maximumFractionDigits: 0 }).format(Math.round(v)));
  }

  // "6.2×10⁻⁷", not JS's native "6.20e-7" -- used for anything that would
  // otherwise fall back to exponential notation.
  function fmtSci(v, lang) {
    if (v === 0) return "0";
    var exp = Math.floor(Math.log10(Math.abs(v)));
    var mantissa = v / Math.pow(10, exp);
    if (Math.abs(mantissa) >= 9.995) { mantissa /= 10; exp += 1; }
    if (Math.abs(mantissa) < 0.995) { mantissa *= 10; exp -= 1; }
    var mantissaStr = trueMinus(intl(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(mantissa));
    return mantissaStr + "×10" + superscript(exp);
  }

  function fmtNum(v, lang) {
    if (v == null) return "—";
    var abs = Math.abs(v);
    if (abs !== 0 && (abs < 0.01 || abs >= 1e5)) return fmtSci(v, lang);
    var digits = abs >= 100 ? 0 : abs >= 1 ? 1 : 2;
    return fmtDecimal(v, lang, digits);
  }

  // GOES long-channel flux (W/m²) -> class letter + multiplier, e.g.
  // "B6.2" -- kept in sync with web/charts.py `xray_class()`. The class
  // notation itself is NOAA's own fixed catalog format (always a period,
  // like "M1.5"), not a localised decimal -- only the flux alongside it is.
  var XRAY_CLASS_FIELD = "0.1-0.8nm";
  var XRAY_BREAKPOINTS = [[1e-4, "X"], [1e-5, "M"], [1e-6, "C"], [1e-7, "B"]];

  function xrayClassLetter(flux) {
    if (!flux || flux <= 0) return null;
    for (var i = 0; i < XRAY_BREAKPOINTS.length; i++) {
      var threshold = XRAY_BREAKPOINTS[i][0], letter = XRAY_BREAKPOINTS[i][1];
      if (flux >= threshold) return letter + (flux / threshold).toFixed(1);
    }
    return "A" + (flux / 1e-8).toFixed(1);
  }

  // The readout's per-unit formatting rule (§1.4): K is grouped (never
  // scientific), W/m² is "class · flux" for the GOES long channel and
  // plain "×10ⁿ" flux for everything else, everything else goes through
  // the general fmtNum.
  function formatReadoutValue(v, panel, s, lang) {
    if (v == null) return "—";
    if (panel.unit === "K") return fmtGrouped(v, lang);
    if (panel.unit === "W/m²") {
      var flux = fmtSci(v, lang);
      if (s.field === XRAY_CLASS_FIELD) {
        var cls = xrayClassLetter(v);
        return cls ? cls + " · " + flux : flux;
      }
      return flux;
    }
    return fmtNum(v, lang);
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
          // The last label ("12:0...") used to clip against the grid's
          // right edge (19-HANDOFF-v0.2.1.md §1.3) -- align it fully inside
          // instead of centring it on a point past the visible area.
          alignMaxLabel: "right", alignMinLabel: "left",
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
      // The resolved extent, filled in by yAxis.min/max below (for a fixed
      // y_range, immediately; for an auto linear axis, by the callback
      // ECharts invokes while building the axis -- always before it builds
      // labels, same setOption pass) and read back by the label formatter
      // to hide any tick within EDGE_PX of the grid's own top/bottom (§1.2:
      // adjacent panels' labels collided at the shared boundary, there
      // being no gap between one grid's bottom and the next one's top).
      var extent = { min: null, max: null };
      var EDGE_PX = 8;
      function nearEdge(v) {
        if (extent.min == null || extent.max == null) return false;
        var frac = isLog
          ? (Math.log10(extent.max) - Math.log10(v)) / (Math.log10(extent.max) - Math.log10(extent.min))
          : (extent.max - v) / (extent.max - extent.min);
        var px = frac * g.H;
        return px < EDGE_PX || px > g.H - EDGE_PX;
      }
      var yAxis = {
        type: isLog ? "log" : "value", gridIndex: pi, scale: !panel.zero_line,
        // A fixed multi-decade log range (X-ray) needs one tick per decade
        // -- splitNumber:3 over 1e-9..1e-2 produced exactly two ticks, the
        // axis extent itself, and nothing in between (§1.1, confirmed
        // interactively via the chart's own zrender scene graph: 0 decade
        // labels existed at any width). An auto-scaled log axis (proton/
        // temperature panels) keeps the old, already-correct default.
        splitNumber: (isLog && panel.y_range) ? Math.round(Math.log10(panel.y_range[1] / panel.y_range[0])) : 3,
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: {
          color: P["--ink-faint"], fontFamily: MONO, fontSize: 10,
          formatter: function (v) {
            if (nearEdge(v)) return "";
            // A linear axis keeps ECharts' own tick-value stringification
            // (it already picks clean "nice" numbers) -- only the edge
            // filter above is new here. A fixed y_range gets one label per
            // decade ("10⁻⁸" style, §1.1); an auto-scaled log axis keeps
            // the old plain exponential.
            if (!isLog) return String(v);
            return panel.y_range ? "10" + superscript(Math.round(Math.log10(v))) : v.toExponential(0);
          },
        },
        splitLine: { show: true, lineStyle: { color: P["--grid"] } },
      };
      if (panel.y_range) {
        yAxis.min = extent.min = panel.y_range[0];
        yAxis.max = extent.max = panel.y_range[1];
      } else if (!isLog) {
        yAxis.min = function (v) { return (extent.min = Math.floor(v.min - (v.max - v.min) * 0.08)); };
        yAxis.max = function (v) { return (extent.max = Math.ceil(v.max + (v.max - v.min) * 0.08)); };
      } else {
        // An auto-scaled log axis (temperature/protons, no fixed y_range):
        // ECharts already picks the extent (its usual "nice" log bounds,
        // left untouched here), just captured for the same edge filter --
        // confirmed by comparing rendered ticks before/after this hook,
        // identical either way.
        yAxis.min = function (v) { return (extent.min = v.min); };
        yAxis.max = function (v) { return (extent.max = v.max); };
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
          // Class letters as borderless markArea labels, centred in each
          // class's own band (19-HANDOFF-v0.2.1.md §1.1) -- a markLine with
          // an invisible (opacity:0) line silently drops its label too in
          // this build, confirmed interactively via the chart's own
          // zrender scene graph (0 text nodes for A/B/C/M/X at any width,
          // next to a visibly-rendered S1 ref line using the same yAxis-on-
          // a-log-axis markLine shape elsewhere): the markArea route both
          // matches the hand-off's own suggestion and is confirmed to work.
          var topEdge = panel.y_range ? panel.y_range[1] : edges[edges.length - 1] * 10;
          panel.log_bands.forEach(function (b, lbi) {
            var hi = panel.log_bands[lbi + 1] ? panel.log_bands[lbi + 1].value : topEdge;
            areas.push([
              {
                yAxis: b.value, itemStyle: { color: "transparent" },
                label: { show: true, formatter: b.label, color: P["--ink-faint"], fontFamily: MONO, fontSize: 9, position: "insideRight" },
              },
              { yAxis: hi },
            ]);
          });
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
        b.textContent = formatReadoutValue(v, p, s, self.i18n.lang);
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
        b.textContent = idx >= 0 ? formatReadoutValue(values[idx], p, s, self.i18n.lang) : "—";
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
        parts.push(s.label + " " + fmtNum(values[idx], self.i18n.lang) + " " + p.unit + (trend ? " (" + trend + ")" : ""));
      });
    });
    if (this.metaEl) {
      var bits = [];
      if (activeSource) bits.push(this.i18n.active_source_label + " " + activeSource);
      // D-082: a delayed product's range ends at its latest datum, and says so.
      var delayed = this.panels.some(function (p) { return p.anchor === "latest"; });
      if (latestTs != null) {
        bits.push(delayed
          ? this.i18n.data_until_label + " " + fmtUT(latestTs, this.i18n) + " " + this.i18n.delayed_suffix
          : this.i18n.last_datum_label + " " + fmtUT(latestTs, this.i18n));
      }
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
    wireZoomAndPng(stack);
  }

  // "Restablecer zoom" + PNG export, shared by the panel stack and the
  // forecast chart: `chart` is anything with a `.root` and a live `.ec`.
  function wireZoomAndPng(chart) {
    var root = chart.root;
    var resetBtn = root.querySelector("[data-reset-zoom]");
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        if (chart.ec) chart.ec.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
      });
    }
    var pngBtn = root.querySelector("[data-png-export]");
    if (pngBtn) {
      pngBtn.addEventListener("click", function () {
        if (!chart.ec) return;
        var P = readPalette();
        var url = chart.ec.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: P["--raised"] });
        var a = document.createElement("a");
        a.href = url;
        a.download = (root.dataset.syncKey || "chart") + ".png";
        document.body.appendChild(a);
        a.click();
        a.remove();
      });
    }
  }

  // ---- forecast chart (v0.4.0 §5) ------------------------------------------
  //
  // One grid, fed by forecast.json (web/forecasts.py). Issue view: the
  // observed target (Kp keeps its severity-coloured bars, D-062), the
  // forecast line with its uncertainty band, the future lightly shaded and
  // a dashed "now" line. Verification view (a lead button): that lead's
  // forecasts against what was then observed, over 7 or 30 days.

  var FORECAST_PAD_MS = 3600 * 1000;

  function forecastHeight() {
    return window.innerWidth < 680 ? 240 : 320;
  }

  function buildForecastOption(fc, d, P) {
    var cfg = fc.cfg, i18n = fc.i18n, g = geom(true), H = forecastHeight();
    var MONO = "JetBrains Mono, ui-monospace, monospace";
    var UI = "Poppins, system-ui, sans-serif";
    var s1 = P["--chart-s1"], series = [];
    var isLead = d.view === "lead";
    var xs = isLead ? d.pairs.valid_at : d.rows.valid_at;
    var fcValues = isLead ? d.pairs.forecast : d.rows.value;
    var lower = isLead ? d.pairs.lower : d.rows.lower;
    var upper = isLead ? d.pairs.upper : d.rows.upper;

    var obsTimes = isLead ? d.pairs.valid_at : ((d.observed && d.observed.time) || []);
    var obsValues = isLead ? d.pairs.observed : ((d.observed && d.observed.series[cfg.field]) || []);
    var nowMs = Date.now();
    var tmin, tmax;
    if (isLead) {
      tmin = xs.length ? xs[0] * 1000 : nowMs - 7 * 86400000;
      tmax = xs.length ? xs[xs.length - 1] * 1000 : nowMs;
    } else {
      tmin = obsTimes.length ? obsTimes[0] * 1000 : (d.issued_at ? d.issued_at * 1000 : nowMs) - 3 * 86400000;
      tmax = Math.max(nowMs, xs.length ? xs[xs.length - 1] * 1000 : nowMs) + FORECAST_PAD_MS;
    }
    fc.spanSeconds = (tmax - tmin) / 1000; // read by .github/scripts/screenshots.py

    // Observed: Kp-style severity bars, or a plain line in the Bt ink.
    if (cfg.bars && !isLead) {
      series.push({
        id: "observed", name: i18n.observed, type: "bar", barMaxWidth: 10, large: true, silent: true, z: 2,
        data: obsTimes.map(function (t, i) {
          var v = obsValues[i];
          return { value: [t * 1000, v], itemStyle: v == null ? undefined : { color: cfg.kp_bucket ? P[kpSeverityVar(v)] : P["--bt"] } };
        }),
      });
    } else {
      series.push({
        id: "observed", name: i18n.observed, type: "line", showSymbol: isLead, symbol: "circle", symbolSize: 4,
        connectNulls: false, lineStyle: { width: 1.5, color: P["--bt"] }, itemStyle: { color: P["--bt"] }, z: 2,
        emphasis: { disabled: true },
        data: obsTimes.map(function (t, i) { return [t * 1000, obsValues[i]]; }),
      });
    }
    if (cfg.bounds) {
      series.push({
        id: "band-lo", type: "line", stack: "fcband", symbol: "none", silent: true, lineStyle: { opacity: 0 }, z: 1,
        data: xs.map(function (t, i) { return [t * 1000, lower[i]]; }),
      });
      series.push({
        id: "band-hi", type: "line", stack: "fcband", symbol: "none", silent: true, lineStyle: { opacity: 0 }, z: 1,
        areaStyle: { color: withAlpha(s1, 0.18) },
        data: xs.map(function (t, i) { return [t * 1000, (upper[i] == null || lower[i] == null) ? null : upper[i] - lower[i]]; }),
      });
    }
    var forecastSeries = {
      id: "forecast", name: i18n.forecast, type: "line", showSymbol: true, symbol: "circle",
      symbolSize: isLead ? 3 : 7, connectNulls: false, z: 3, emphasis: { disabled: true },
      lineStyle: { width: 2, color: s1 }, itemStyle: { color: s1 },
      data: xs.map(function (t, i) { return [t * 1000, fcValues[i]]; }),
    };
    series.push(forecastSeries);

    var ml = (cfg.ref_lines || []).map(function (r) {
      return {
        yAxis: r.value, lineStyle: { color: P["--ref"], width: 1, type: "dashed" },
        label: { show: true, formatter: r.label, color: P["--ink-faint"], fontFamily: MONO, fontSize: 9, position: "insideEndTop" },
      };
    });
    if (!isLead) {
      ml.push({
        xAxis: nowMs, lineStyle: { color: P["--ink-soft"], width: 1, type: "dashed" },
        label: { show: true, formatter: i18n.now, color: P["--ink-soft"], fontFamily: MONO, fontSize: 9, position: "insideEndTop" },
      });
      forecastSeries.markArea = {
        silent: true, data: [[{ xAxis: nowMs, itemStyle: { color: withAlpha(P["--ink-faint"], 0.08) } }, { xAxis: tmax }]],
      };
    }
    if (ml.length) forecastSeries.markLine = { silent: true, symbol: "none", animation: false, data: ml };

    var yAxis = {
      type: "value", scale: true, splitNumber: 4,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: P["--ink-faint"], fontFamily: MONO, fontSize: 10, formatter: function (v) { return String(v); } },
      splitLine: { show: true, lineStyle: { color: P["--grid"] } },
    };
    if (cfg.y_range) { yAxis.min = cfg.y_range[0]; yAxis.max = cfg.y_range[1]; }

    return {
      useUTC: true, animation: false, textStyle: { fontFamily: MONO },
      title: [{
        text: cfg.title + (cfg.unit ? "  ·  " + cfg.unit : ""), left: g.left + 8, top: g.top + 5,
        textStyle: { fontFamily: UI, fontSize: 10, fontWeight: 500, color: P["--ink-faint"] },
      }],
      grid: [{ left: g.left, right: g.right, top: g.top, height: H, show: true, borderColor: P["--line"], borderWidth: 1 }],
      xAxis: [{
        type: "time", min: tmin, max: tmax,
        axisLine: { show: false }, axisTick: { lineStyle: { color: P["--line"] } },
        axisLabel: {
          color: P["--ink-faint"], fontFamily: MONO, fontSize: 10, hideOverlap: true,
          alignMaxLabel: "right", alignMinLabel: "left",
          formatter: function (v) {
            var dd = new Date(v);
            return (dd.getUTCHours() === 0 && dd.getUTCMinutes() === 0) ? "{d|" + fmtDate(dd, i18n) + "}" : fmtTime(dd);
          },
          rich: { d: { fontWeight: 600, color: P["--ink-soft"], fontFamily: MONO, fontSize: 10 } },
        },
        splitLine: { show: true, lineStyle: { color: P["--grid"] } },
        name: i18n.time_axis_label, nameLocation: "end", nameGap: 4,
        nameTextStyle: { color: P["--ink-faint"], fontFamily: MONO, fontSize: 9 },
      }],
      yAxis: [yAxis],
      series: series,
      axisPointer: { lineStyle: { color: P["--ref"], type: "dashed" }, label: { show: false } },
      tooltip: { trigger: "axis", showContent: false, axisPointer: { type: "line" } },
      dataZoom: [{ type: "inside", xAxisIndex: [0], filterMode: "none" }],
    };
  }

  function nearestIndex(times, tsSec, maxDist) {
    var idx = -1, best = Infinity;
    for (var i = 0; i < times.length; i++) {
      var diff = Math.abs(times[i] - tsSec);
      if (diff < best) { best = diff; idx = i; }
    }
    return best <= maxDist ? idx : -1;
  }

  function ForecastChart(root) {
    this.root = root;
    this.cfg = JSON.parse(document.getElementById(root.dataset.configId).textContent);
    this.i18n = JSON.parse(document.getElementById(root.dataset.i18nId).textContent);
    this.plotEl = root.querySelector("[data-stack-plot]");
    this.readEl = root.querySelector("[data-fc-read]");
    this.metaEl = root.querySelector("[data-fc-meta]");
    this.snapEl = root.querySelector("[data-fc-snap]");
    this.ec = null;
    this.data = null;
    this.cache = new Map(); // query -> {data, fetchedAt}
    this.controller = null;
    this.view = "issue";
    this.lead = null;
    this.ranges = { issue: root.dataset.range, lead: "7d" };
    this.statusEl = document.createElement("p");
    this.statusEl.className = "swx-stack-status";
    this.statusEl.hidden = true;
    this.plotEl.appendChild(this.statusEl);
    root.__swxForecast = this; // read by .github/scripts/screenshots.py
  }

  ForecastChart.prototype.query = function () {
    if (this.view === "lead") return "?lead=" + encodeURIComponent(this.lead) + "&range=" + encodeURIComponent(this.ranges.lead);
    return "?range=" + encodeURIComponent(this.ranges.issue);
  };

  ForecastChart.prototype.load = function () {
    var self = this, q = this.query();
    this.root.dataset.query = q;
    var hit = this.cache.get(q);
    if (this.controller) this.controller.abort();
    if (hit && Date.now() - hit.fetchedAt < CACHE_TTL_MS) { this.render(hit.data); return; }
    var mine = (this.controller = window.AbortController ? new AbortController() : null);
    fetch(this.cfg.endpoint + q, { signal: mine ? mine.signal : undefined })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) {
        if (self.root.dataset.query !== q) return; // a newer click owns the chart
        self.cache.set(q, { data: data, fetchedAt: Date.now() });
        self.render(data);
      }, function (err) {
        if (err && err.name === "AbortError") return;
        if (self.root.dataset.query !== q) return;
        self.render(null);
      });
  };

  ForecastChart.prototype.render = function (data) {
    this.data = data;
    var P = readPalette(), g = geom(true), H = forecastHeight();
    this.plotEl.style.height = (g.top + H + g.bottom) + "px";
    this.statusEl.style.top = g.top + "px";
    this.statusEl.style.height = H + "px";
    var empty = !data || (data.view === "issue" ? !data.rows.valid_at.length : !data.pairs.valid_at.length);
    this.statusEl.hidden = !empty;
    this.statusEl.textContent = !data ? this.i18n.error_message : (data.view === "issue" ? this.i18n.no_issue : this.i18n.no_data_message);
    if (!this.ec) this.ec = echarts.init(this.plotEl, null, { renderer: "canvas" }); // eslint-disable-line no-undef
    if (data) this.ec.setOption(buildForecastOption(this, data, P), { notMerge: true });
    this.plotEl.dataset.echartsReady = "true";
    this.renderMeta();
    this.setReadout(null);
    var self = this;
    this.ec.off("updateAxisPointer");
    this.ec.on("updateAxisPointer", function (e) {
      var info = e.axesInfo && e.axesInfo[0];
      self.setReadout(info ? Math.round(info.value / 1000) : null);
    });
    this.ec.getZr().off("globalout");
    this.ec.getZr().on("globalout", function () { self.setReadout(null); });
  };

  ForecastChart.prototype.renderMeta = function () {
    var d = this.data, i18n = this.i18n, bits = [];
    if (d && d.view === "issue" && d.issued_at) bits.push(i18n.issued + " " + fmtUT(d.issued_at, i18n));
    // D-079: how fresh the issue's input was, so a stale target shows.
    if (d && d.view === "issue" && d.issued_at && d.data_until) bits.push(i18n.data_until + " " + fmtUT(d.data_until, i18n));
    if (d && d.view === "lead") bits.push(i18n.lead_label + " " + Math.round(d.lead_seconds / 3600) + " h");
    if (d && d.model_version) bits.push("v" + d.model_version);
    if (this.metaEl) this.metaEl.textContent = bits.join(" · ");
    if (this.snapEl) {
      var legend = [];
      if (d && d.view === "issue" && d.issued_at) {
        if (this.cfg.bounds) legend.push(i18n.band_note);
        legend.push(i18n.future_note);
      }
      this.snapEl.textContent = legend.join(" · ");
    }
    this.plotEl.setAttribute("aria-label", (this.root.dataset.ariaLabel || "") + (bits.length ? ": " + bits.join(", ") : ""));
  };

  // Readout: observed, forecast and its interval at the cursor, or (no
  // cursor) the issue's first lead / the newest verified pair.
  ForecastChart.prototype.setReadout = function (tsSec) {
    var d = this.data, i18n = this.i18n, lang = i18n.lang;
    if (!this.readEl) return;
    var obs = null, fcv = null, lo = null, hi = null, when = null;
    if (d) {
      var isLead = d.view === "lead";
      var xs = isLead ? d.pairs.valid_at : d.rows.valid_at;
      var fi = tsSec == null ? (isLead ? xs.length - 1 : 0) : nearestIndex(xs, tsSec, isLead ? 5400 : 3 * 3600);
      if (fi >= 0 && fi < xs.length) {
        fcv = isLead ? d.pairs.forecast[fi] : d.rows.value[fi];
        lo = isLead ? d.pairs.lower[fi] : d.rows.lower[fi];
        hi = isLead ? d.pairs.upper[fi] : d.rows.upper[fi];
        when = xs[fi];
        if (isLead) obs = d.pairs.observed[fi];
      }
      if (!isLead && d.observed) {
        var ot = d.observed.time || [], ov = d.observed.series[this.cfg.field] || [];
        var oi = tsSec == null ? lastIndexWithValue(ov) : nearestIndex(ot, tsSec, 3 * 3600);
        obs = oi >= 0 ? ov[oi] : null;
      }
    }
    var html = '<div class="rrow fc-read">';
    html += '<div><span class="k"><span class="sw" style="background:' + readPalette()["--bt"] + '"></span>' + escapeHtml(i18n.observed) + "</span><b>" + fmtNum(obs, lang) + "</b></div>";
    html += '<div><span class="k"><span class="sw" style="background:' + readPalette()["--chart-s1"] + '"></span>' + escapeHtml(i18n.forecast) + "</span><b>" + fmtNum(fcv, lang) + "</b></div>";
    if (this.cfg.bounds) {
      html += "<div><span class=\"k\">" + escapeHtml(i18n.band) + "</span><b>" + (lo == null ? "—" : fmtNum(lo, lang) + "–" + fmtNum(hi, lang)) + "</b></div>";
    }
    if (when != null) html += '<div><span class="unit">' + escapeHtml(i18n.valid_label + " " + fmtUT(when, i18n)) + "</span></div>";
    html += "</div>";
    this.readEl.innerHTML = html;
  };

  ForecastChart.prototype.resize = function () {
    if (!this.ec) return;
    var g = geom(true);
    this.plotEl.style.height = (g.top + forecastHeight() + g.bottom) + "px";
    this.ec.resize();
  };

  function initForecast(root) {
    var fc = new ForecastChart(root);
    var viewRow = root.querySelector("[data-fc-views]");
    var rangeRows = root.querySelectorAll("[data-fc-ranges]");
    function press(row, btn) {
      row.querySelectorAll("button").forEach(function (b) {
        var on = b === btn;
        b.classList.toggle("on", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }
    if (viewRow) {
      viewRow.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-fc-view]");
        if (!btn) return;
        press(viewRow, btn);
        fc.view = btn.dataset.fcView;
        fc.lead = btn.dataset.fcLead || null;
        root.dataset.view = fc.view;
        rangeRows.forEach(function (row) { row.hidden = row.dataset.fcRanges !== fc.view; });
        fc.load();
      });
    }
    rangeRows.forEach(function (row) {
      row.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-fc-range]");
        if (!btn) return;
        press(row, btn);
        fc.ranges[row.dataset.fcRanges] = btn.dataset.fcRange;
        fc.load();
      });
    });
    wireZoomAndPng(fc);
    liveForecasts.push(fc);
    if (window.ResizeObserver) {
      var lastWidth = root.clientWidth;
      new ResizeObserver(function () {
        var w = root.clientWidth;
        if (w === lastWidth) return;
        lastWidth = w;
        fc.resize();
      }).observe(root);
    }
    fc.load();
  }

  var liveForecasts = [];

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
    document.querySelectorAll(".swx-forecast").forEach(initForecast);
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
    liveForecasts.forEach(function (fc) {
      if (fc.ec) { fc.ec.dispose(); fc.ec = null; }
      fc.render(fc.data);
    });
  });
})();

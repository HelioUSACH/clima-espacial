/* Wires vendored uPlot (web/static/web/vendor/uplot/) to the internal
 * `/productos/<id>/serie.json` endpoint. No build step, no CDN (D-002):
 * this file is plain ES2017, loaded with <script defer>.
 *
 * Mark spec (dataviz skill): 2px lines, hairline recessive gridlines,
 * uPlot's built-in legend+cursor tooltip satisfies "legend always present"
 * and "hover layer by default" for ≥1 series without extra code.
 */
(function () {
  "use strict";

  var DARK = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  var INK = DARK ? "#ffffff" : "#0b0b0b";
  var INK_MUTED = "#898781";
  var GRID = DARK ? "#2c2c2a" : "#e1e0d9";
  var AXIS = DARK ? "#383835" : "#c3c2b7";

  function buildSeries(fields) {
    var series = [{}]; // x-axis placeholder (uPlot convention)
    fields.forEach(function (f) {
      series.push({
        label: f.label,
        stroke: f.color,
        width: 2 / (window.devicePixelRatio || 1),
        points: { show: false },
      });
    });
    return series;
  }

  function axisOpts(extra) {
    return Object.assign(
      {
        stroke: INK_MUTED,
        grid: { stroke: GRID, width: 1 },
        ticks: { stroke: AXIS, width: 1 },
      },
      extra || {}
    );
  }

  function render(el, fields, payload) {
    var unit = fields.length && fields[0].unit ? " (" + fields[0].unit + ")" : "";
    var opts = {
      width: el.clientWidth || 600,
      height: 220,
      series: buildSeries(fields),
      axes: [axisOpts({}), axisOpts({ label: unit.trim() ? unit.trim() : undefined })],
      cursor: { points: { size: 8 } },
      legend: { show: true },
    };
    var data = [payload.time];
    fields.forEach(function (f) {
      data.push(payload.series[f.key] || []);
    });
    // eslint-disable-next-line no-undef
    new uPlot(opts, data, el);
  }

  function renderEmpty(el, message) {
    el.textContent = message;
    el.classList.add("swx-chart--empty");
  }

  function initChart(el) {
    var range = el.dataset.range || "1d";
    var fieldsEl = document.getElementById(el.dataset.fieldsId);
    var fields = fieldsEl ? JSON.parse(fieldsEl.textContent) : [];
    var noDataMsg = el.dataset.noDataMessage || "Sin datos todavía.";
    var errorMsg = el.dataset.errorMessage || "No se pudo cargar la serie.";

    fetch(el.dataset.endpoint + "?range=" + encodeURIComponent(range))
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (payload) {
        if (!payload.time || !payload.time.length) {
          renderEmpty(el, noDataMsg);
          return;
        }
        render(el, fields, payload);
      })
      .catch(function () {
        renderEmpty(el, errorMsg);
      });
  }

  function initAll() {
    document.querySelectorAll(".swx-chart[data-product]").forEach(initChart);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Range toggle (1d/3d/7d): re-render in place instead of a page reload.
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-chart-range]");
    if (!btn) return;
    var wrapper = btn.closest(".swx-chart-block");
    if (!wrapper) return;
    var el = wrapper.querySelector(".swx-chart");
    el.dataset.range = btn.dataset.chartRange;
    el.innerHTML = "";
    el.classList.remove("swx-chart--empty");
    wrapper.querySelectorAll("[data-chart-range]").forEach(function (b) {
      b.setAttribute("aria-pressed", b === btn ? "true" : "false");
    });
    initChart(el);
  });
})();

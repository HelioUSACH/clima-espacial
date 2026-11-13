/* Local time next to every UT time on the page (D-105).
 *
 * The server renders each time as <time datetime="…Z" data-ut="dt|t|ts|dts">
 * with its UT text (web/timefmt.py). This file adds the visitor's own local
 * time next to it, in the browser's time zone
 * (Intl.DateTimeFormat().resolvedOptions().timeZone): nothing is sent
 * anywhere. "21:34 UT (18:34 hora local)"; a <time data-stack> (a table
 * cell, D-108) puts the second one on its own line, without parentheses,
 * small and muted. The header's "UT / hora local"
 * switch picks which one comes first; the choice is kept in localStorage
 * (every access in try/catch; default UT). With JavaScript off the page
 * shows UT, as it always did.
 *
 * window.swxTime is the same formatting for text built in the browser (the
 * chart readout, the image player): swxTime.parts(seconds, opts) gives
 * {primary, secondary} strings, swxTime.html(seconds, opts) the escaped
 * markup. A "swx:timechange" event on document follows the switch.
 * No build step, no CDN (D-002): plain ES2017, loaded deferred.
 */
(function () {
  "use strict";

  var KEY = "swx-time";
  var root = document.documentElement;
  var lang = root.getAttribute("lang") === "en" ? "en" : "es";
  var LABEL = lang === "en" ? "local time" : "hora local";
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  var formatter = null;
  try {
    var zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    });
  } catch (e) { formatter = null; }

  function pad2(n) { return n < 10 ? "0" + n : "" + n; }

  function utcParts(d) {
    return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate(),
             H: d.getUTCHours(), M: d.getUTCMinutes(), S: d.getUTCSeconds() };
  }

  function localParts(d) {
    if (!formatter) return null;
    var out = {};
    try {
      formatter.formatToParts(d).forEach(function (p) {
        if (p.type === "year") out.y = +p.value;
        else if (p.type === "month") out.m = +p.value;
        else if (p.type === "day") out.d = +p.value;
        else if (p.type === "hour") out.H = +p.value % 24;
        else if (p.type === "minute") out.M = +p.value;
        else if (p.type === "second") out.S = +p.value;
      });
    } catch (e) { return null; }
    return out.y ? out : null;
  }

  function fmt(p, withDate, seconds) {
    var clock = pad2(p.H) + ":" + pad2(p.M) + (seconds ? ":" + pad2(p.S) : "");
    if (!withDate) return clock;
    var day = lang === "en" ? pad2(p.d) + " " + MONTHS[p.m - 1] : pad2(p.d) + "/" + pad2(p.m);
    return day + " " + clock;
  }

  function readPref() {
    try { return localStorage.getItem(KEY) === "local" ? "local" : "ut"; } catch (e) { return "ut"; }
  }
  var pref = readPref();

  // {primary, secondary}: secondary is null when local time is UT here.
  // opts.date: the UT text carries a date; opts.seconds: and seconds.
  function parts(seconds, opts) {
    opts = opts || {};
    var d = new Date(seconds * 1000);
    var U = utcParts(d), L = localParts(d);
    var utText = fmt(U, !!opts.date, !!opts.seconds) + " UT";
    if (!L || (L.y === U.y && L.m === U.m && L.d === U.d && L.H === U.H && L.M === U.M)) {
      return { primary: utText, secondary: null };
    }
    var dayDiffers = L.y !== U.y || L.m !== U.m || L.d !== U.d;
    if (pref === "local") {
      return {
        primary: fmt(L, !!opts.date || dayDiffers, !!opts.seconds) + " " + LABEL,
        secondary: fmt(U, dayDiffers, !!opts.seconds) + " UT",
      };
    }
    return { primary: utText, secondary: fmt(L, dayDiffers, !!opts.seconds) + " " + LABEL };
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function html(seconds, opts) {
    var p = parts(seconds, opts);
    return escapeHtml(p.primary) + (p.secondary ? ' <span class="t-alt">(' + escapeHtml(p.secondary) + ")</span>" : "");
  }

  function renderTime(el) {
    var t = Date.parse(el.getAttribute("datetime"));
    if (isNaN(t)) return;
    var kind = el.getAttribute("data-ut") || "dt";
    if (el.__swxUT == null) el.__swxUT = el.textContent;
    var p = parts(t / 1000, { date: kind.charAt(0) === "d", seconds: kind.slice(-1) === "s" });
    el.textContent = "";
    if (!p.secondary) { el.textContent = el.__swxUT; return; }
    // With UT first, keep the server's own UT text exactly.
    el.appendChild(document.createTextNode(pref === "ut" ? el.__swxUT : p.primary));
    var alt = document.createElement("span");
    if (el.hasAttribute("data-stack")) {
      alt.className = "t-alt t-line";
      alt.textContent = p.secondary;
    } else {
      alt.className = "t-alt";
      alt.textContent = " (" + p.secondary + ")";
    }
    el.appendChild(alt);
  }

  function renderAll(scope) {
    (scope || document).querySelectorAll("time[data-ut]").forEach(renderTime);
  }

  // Is local time ever not UT for this visitor (now)? If not, no switch.
  function localDiffers() {
    var now = new Date(), L = localParts(now), U = utcParts(now);
    return !!L && (L.H !== U.H || L.M !== U.M || L.d !== U.d);
  }

  function syncSwitch(sw) {
    sw.querySelectorAll("[data-tz]").forEach(function (b) {
      var on = b.getAttribute("data-tz") === pref;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function setPref(next) {
    pref = next === "local" ? "local" : "ut";
    try { localStorage.setItem(KEY, pref); } catch (e) { /* private mode: this page only */ }
    root.setAttribute("data-time", pref);
    document.querySelectorAll("[data-tz-switch]").forEach(syncSwitch);
    renderAll();
    document.dispatchEvent(new CustomEvent("swx:timechange", { detail: { pref: pref } }));
  }

  window.swxTime = {
    parts: parts,
    html: html,
    label: LABEL,
    pref: function () { return pref; },
    render: renderAll,
  };

  function init() {
    root.setAttribute("data-time", pref);
    renderAll();
    if (!localDiffers()) return;
    document.querySelectorAll("[data-tz-switch]").forEach(function (sw) {
      sw.hidden = false;
      syncSwitch(sw);
      sw.addEventListener("click", function (ev) {
        var b = ev.target.closest("[data-tz]");
        if (b) setPref(b.getAttribute("data-tz"));
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

/* The flare detector's page (D-101, D-102, D-106..D-108) and the home
 * page's "El Sol ahora" block (D-104, D-106). The images come from nginx's
 * cached image locations (D-100); this file only swaps their URLs and wires
 * the frame player (flare-player.js) to the page:
 *
 * - flare page, images: the grid of eight tiles (four channels, image and
 *   difference); a tile opens in a lightbox (<dialog>: Esc, the close
 *   button or a click outside closes it, focus returns to the tile). Under
 *   the grid, the 6-h loop of 171 Å with its "imagen / diferencia" switch;
 *   switching while it plays reloads it on the other channel.
 * - flare page, events: the alert table is the selector. A click on a row
 *   (or its "ver" link), or Enter/Space on a focused row, shows that
 *   event's panel (cloned from its server-rendered <template>) and puts
 *   ?evento=<id> in the URL; ↑/↓ move between rows. The panel: Grad-CAM
 *   over the peak's 131 Å image (the live image, then the replay's stored
 *   copy, then the map alone), its opacity slider, and the replay played
 *   in the same frame, with its X-ray chart whose cursor follows the frame.
 * - home: four thumbnails in a fixed order; the selected one is shown
 *   large and highlighted, a click never reorders them. The block hides
 *   itself when its image can't be had (service down and nothing cached).
 *
 * No build step, no CDN (D-002): plain ES2017, loaded deferred.
 */
(function () {
  "use strict";

  var reducedMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  function frameOf(img) { return img.closest(".flare-frame"); }

  function markMissing(img, missing) {
    var frame = frameOf(img);
    if (!frame) return;
    var note = frame.querySelector(".flare-missing");
    frame.classList.toggle("is-missing", missing);
    if (note) note.hidden = !missing;
  }

  function watch(img, onError) {
    img.addEventListener("error", function () { (onError || markMissing)(img, true); });
    img.addEventListener("load", function () { if (!onError) markMissing(img, false); });
    // An image that failed before this deferred script ran.
    if (img.complete && img.naturalWidth === 0 && img.getAttribute("src")) (onError || markMissing)(img, true);
  }

  function pressOnly(group, btn) {
    group.querySelectorAll("button").forEach(function (b) {
      var on = b === btn;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function player(id) {
    var el = document.getElementById(id);
    return el && el.__swxPlayer;
  }

  function busy(p) { return !!p && (p.frames.length > 0 || p.root.hasAttribute("aria-busy")); }

  function withQuery(url, params) {
    var q = Object.keys(params).map(function (k) { return k + "=" + encodeURIComponent(params[k]); }).join("&");
    return url + (url.indexOf("?") < 0 ? "?" : "&") + q;
  }

  // ---- flare page: the image grid and its lightbox ---------------------------

  function initGrid(page) {
    var grid = page.querySelector("[data-flare-grid]");
    var box = page.querySelector("[data-flare-lightbox]");
    if (!grid || !box) return;
    var img = box.querySelector("[data-lb-img]");
    var title = box.querySelector("[data-lb-title]");
    var when = box.querySelector("[data-lb-when]");
    var opener = null;
    grid.querySelectorAll("[data-tile] img").forEach(function (i) { watch(i); });
    watch(img);

    function close() {
      if (box.open) box.close();
    }

    grid.addEventListener("click", function (ev) {
      var tile = ev.target.closest("[data-tile]");
      if (!tile) return;
      var fig = tile.closest(".flare-tile");
      var t = fig.querySelector("[data-tile-title]");
      var w = fig.querySelector("[data-tile-when]");
      opener = tile;
      markMissing(img, false);
      img.setAttribute("src", tile.dataset.full);
      img.alt = "SDO/AIA " + (t ? t.textContent : tile.dataset.wave + " Å");
      title.textContent = t ? t.textContent : "";
      when.innerHTML = w ? w.innerHTML : "";
      if (typeof box.showModal === "function") box.showModal();
      else box.setAttribute("open", "");
      box.querySelector("[data-lb-close]").focus();
    });
    box.querySelector("[data-lb-close]").addEventListener("click", close);
    // A click on the backdrop (the dialog itself, outside its content).
    box.addEventListener("click", function (ev) { if (ev.target === box) close(); });
    box.addEventListener("close", function () {
      if (opener) opener.focus();
      opener = null;
    });
  }

  // ---- flare page: the 6-h loop -----------------------------------------------

  function initLoop(page) {
    var loopRoot = document.getElementById("flare-loop");
    var kinds = page.querySelector("[data-flare-kind]");
    var frame = document.getElementById("flare-loop-frame");
    if (!loopRoot || !kinds) return;
    var base = loopRoot.dataset.src;
    var kind = "img";

    function src() { return withQuery(base, { channel: kind === "diff" ? "171-diff" : "171", hours: 6 }); }
    loopRoot.dataset.src = src();

    kinds.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-kind]");
      if (!btn || btn.dataset.kind === kind) return;
      kind = btn.dataset.kind;
      pressOnly(kinds, btn);
      loopRoot.dataset.src = src();
      var p = player("flare-loop");
      if (busy(p)) p.load(loopRoot.dataset.src);
    });
    loopRoot.addEventListener("swx:player-start", function () { if (frame) frame.hidden = false; });
  }

  // ---- flare page: events --------------------------------------------------------

  function fill(template, values) {
    return String(template).replace(/%\((\w+)\)s/g, function (_m, k) { return values[k] != null ? values[k] : ""; });
  }

  function initEvents(page) {
    var section = page.querySelector("[data-flare-events]");
    var panel = page.querySelector("[data-flare-ev]");
    if (!section || !panel) return;
    var body = panel.querySelector("[data-ev-body]");
    var replayBox = panel.querySelector("[data-ev-replay]");
    var note = panel.querySelector("[data-replay-note]");
    var chartRoot = panel.querySelector("[data-mount-later]");
    var rows = Array.prototype.slice.call(section.querySelectorAll("tr[data-ev]"));
    var i18n = JSON.parse(document.getElementById("fp-i18n").textContent);
    var current = null;
    var stack = null;
    var pendingXray = null;

    function closeReplay() {
      var p = player("flare-replay-player");
      if (p) p.reset();
      replayBox.hidden = true;
      stack = null;
      pendingXray = null;
      body.querySelectorAll("[data-replay]").forEach(function (b) { b.hidden = false; });
      body.querySelectorAll("[data-replay-close]").forEach(function (b) { b.hidden = true; });
      body.querySelectorAll("[data-cam-opacity-row]").forEach(function (r) { r.hidden = false; });
    }

    function wirePanel() {
      var heat = body.querySelector(".flare-cam-heat");
      var base = body.querySelector(".flare-cam-base");
      var slider = body.querySelector("[data-cam-opacity]");
      var noBase = body.querySelector("[data-cam-nobase]");
      if (heat) watch(heat);
      if (heat && slider) slider.addEventListener("input", function () { heat.style.opacity = String(+slider.value / 100); });
      if (base) {
        // The live 131 Å image (flare-nrt keeps 4 days), then the replay's
        // own copy (1.2.0), then the map alone: it carries the solar limb.
        watch(base, function () {
          var next = base.dataset.fallback;
          if (next) {
            base.removeAttribute("data-fallback");
            base.setAttribute("src", next);
            return;
          }
          base.hidden = true;
          if (noBase) noBase.hidden = false;
        });
      }
      var start = body.querySelector("[data-replay]");
      var stop = body.querySelector("[data-replay-close]");
      if (start) {
        start.addEventListener("click", function () {
          var p = player("flare-replay-player");
          if (!p) return;
          replayBox.hidden = false;
          start.hidden = true;
          if (stop) stop.hidden = false;
          body.querySelectorAll("[data-cam-opacity-row]").forEach(function (r) { r.hidden = true; });
          note.hidden = true;
          note.textContent = "";
          p.onFrame = function (f) { if (window.swxCharts && stack) window.swxCharts.cursor(stack, f.t); };
          // The X-ray chart is mounted once the replay's own data is in:
          // both need the service's replay.json server-side, and a request
          // that arrives while another fetches it gets nothing rather than
          // a wait (D-100).
          pendingXray = start.dataset.replayXray;
          stack = null;
          p.load(start.dataset.replay);
        });
      }
      if (stop) stop.addEventListener("click", function () { closeReplay(); if (start) start.focus(); });
    }

    function select(id, opts) {
      opts = opts || {};
      if (id === current) return;
      var tpl = document.getElementById("flare-ev-tpl-" + id);
      if (!tpl) return;
      closeReplay();
      body.innerHTML = "";
      body.appendChild(tpl.content.cloneNode(true));
      if (window.swxTime) window.swxTime.render(body);
      current = id;
      rows.forEach(function (r) {
        var on = r.dataset.ev === id;
        r.classList.toggle("is-sel", on);
        if (on) r.setAttribute("aria-current", "true");
        else r.removeAttribute("aria-current");
      });
      wirePanel();
      try {
        var url = new URL(window.location.href);
        url.searchParams.set("evento", id);
        url.hash = "";
        window.history.replaceState(null, "", url.toString());
      } catch (e) { /* an old browser: the URL just doesn't follow */ }
      // The panel is under the table: bring it into view if it isn't.
      var top = panel.getBoundingClientRect().top;
      if (opts.scroll !== false && top > window.innerHeight - 80) {
        panel.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
      }
    }

    rows.forEach(function (row, i) {
      row.addEventListener("click", function (ev) {
        if (ev.target.closest("a[data-ev-link]")) ev.preventDefault();
        select(row.dataset.ev);
      });
      row.addEventListener("keydown", function (ev) {
        if (ev.target !== row) return;
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          select(row.dataset.ev);
        } else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
          var next = rows[i + (ev.key === "ArrowDown" ? 1 : -1)];
          if (next) { ev.preventDefault(); next.focus(); }
        }
      });
    });

    panel.addEventListener("swx:player-data", function (ev) {
      var d = ev.detail || {};
      if (d.unavailable) { note.textContent = i18n.unavailable; note.hidden = false; return; }
      note.hidden = true;
      if (pendingXray && window.swxCharts) {
        stack = window.swxCharts.mount(chartRoot, pendingXray);
        pendingXray = null;
      }
      if (d.complete === false) {
        note.textContent = fill(i18n.incomplete, { n171: d.missing["171"], slots: d.n_slots != null ? d.n_slots : "—",
                                                   ncam: d.missing["gradcam-heat"] });
        if (d.note) {
          var src = document.createElement("small");
          src.lang = "en";
          src.textContent = " " + d.note;
          note.appendChild(src);
        }
        note.hidden = false;
      }
    });

    var selected = section.querySelector("tr[data-ev].is-sel");
    current = selected ? selected.dataset.ev : null;
    wirePanel();
    // A link to an event (?evento=<id>): show it.
    if (/[?&]evento=\d/.test(window.location.search) && current) {
      panel.scrollIntoView({ block: "start" });
    }
    // For .github/scripts/screenshots.py and tests.
    panel.__swxSelect = function (id) { select(String(id), { scroll: false }); };
  }

  // ---- home: El Sol ahora -------------------------------------------------------

  function initSunNow() {
    var block = document.querySelector("[data-sun-now]");
    if (!block) return;
    var main = block.querySelector("[data-sun-main]");
    var caption = block.querySelector("[data-sun-wave]");
    var group = block.querySelector(".sun-thumbs");
    // Service down and nothing in nginx's cache: the block goes away; the
    // rest of the home page doesn't depend on it.
    watch(main, function () {
      var p = player("sun-loop");
      if (p && p.frames.length) return;
      block.hidden = true;
    });
    function show(btn) {
      var p = player("sun-loop");
      if (btn.dataset.wave !== "171" && busy(p)) p.reset();
      main.setAttribute("src", btn.dataset.large);
      main.dataset.wave = btn.dataset.wave;
      main.alt = "SDO/AIA " + btn.dataset.wave + " Å";
      caption.textContent = btn.dataset.wave + " Å";
      pressOnly(group, btn);
    }
    block.querySelectorAll("[data-sun-thumb]").forEach(function (btn) {
      btn.addEventListener("click", function () { if (btn.getAttribute("aria-pressed") !== "true") show(btn); });
    });
    var loopRoot = document.getElementById("sun-loop");
    if (loopRoot) {
      // The loop is 171 Å: select 171 first.
      loopRoot.addEventListener("swx:player-start", function () {
        if (main.dataset.wave === "171") return;
        var back = block.querySelector('[data-sun-thumb][data-wave="171"]');
        if (back) show(back);
      });
    }
  }

  function init() {
    var page = document.querySelector(".flare-page");
    if (page) {
      initGrid(page);
      initLoop(page);
      initEvents(page);
    }
    initSunNow();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

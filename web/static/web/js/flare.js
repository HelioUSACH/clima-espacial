/* The flare detector's page (D-101, D-102) and the home page's "El Sol
 * ahora" block (D-104). The images come from nginx's cached image locations
 * (D-100); this file only swaps their URLs and wires the frame player
 * (flare-player.js) to the page:
 *
 * - flare page: channel buttons (131/171/304/1600) and "imagen /
 *   diferencia" for the one large image; the 6-h loop follows the toggle
 *   (171 Å or its difference) and any button press stops it; the Grad-CAM
 *   opacity slider, and the heat map alone when its 131 Å image is gone;
 *   "ver repetición" opens the replay player with its X-ray chart, whose
 *   cursor follows the frame.
 * - home: a thumbnail swaps with the large image; the block hides itself
 *   when its image can't be had (service down and nothing cached).
 *
 * No build step, no CDN (D-002): plain ES2017, loaded deferred.
 */
(function () {
  "use strict";

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

  function withQuery(url, params) {
    var q = Object.keys(params).map(function (k) { return k + "=" + encodeURIComponent(params[k]); }).join("&");
    return url + (url.indexOf("?") < 0 ? "?" : "&") + q;
  }

  // ---- flare page: the large image ------------------------------------------

  function initViewer(page) {
    var viewer = page.querySelector("[data-flare-viewer]");
    if (!viewer) return;
    var main = viewer.querySelector("[data-flare-main]");
    var waves = viewer.querySelector("[data-flare-waves]");
    var kinds = viewer.querySelector("[data-flare-kind]");
    var caption = viewer.querySelector("[data-flare-caption]");
    var loopRoot = document.getElementById("flare-loop");
    var loopBase = loopRoot ? loopRoot.dataset.src : null;
    var state = { wave: "171", kind: "img" };
    watch(main);

    function apply() {
      var btn = waves.querySelector('[data-wave="' + state.wave + '"]');
      var next = state.kind === "diff" ? btn.dataset.diff : btn.dataset.img;
      if (main.getAttribute("src") !== next) main.setAttribute("src", next);
      main.alt = "SDO/AIA " + state.wave + " Å";
      caption.textContent = state.wave + " Å";
      viewer.querySelectorAll("[data-tobs-for]").forEach(function (s) { s.hidden = s.dataset.tobsFor !== state.wave; });
      pressOnly(waves, btn);
      pressOnly(kinds, kinds.querySelector('[data-kind="' + state.kind + '"]'));
      if (loopRoot && loopBase) {
        loopRoot.dataset.src = withQuery(loopBase, { channel: state.kind === "diff" ? "171-diff" : "171", hours: 6 });
      }
    }

    function stopLoop() {
      var p = player("flare-loop");
      if (p && (p.frames.length || p.root.hasAttribute("aria-busy"))) p.reset();
    }

    waves.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-wave]");
      if (!btn) return;
      stopLoop();
      state.wave = btn.dataset.wave;
      apply();
    });
    kinds.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-kind]");
      if (!btn) return;
      stopLoop();
      state.kind = btn.dataset.kind;
      apply();
    });
    // The loop is 171 Å: the still behind it follows.
    if (loopRoot) {
      loopRoot.addEventListener("swx:player-start", function () {
        if (state.wave !== "171") { state.wave = "171"; apply(); }
      });
    }
    apply();
  }

  // ---- flare page: Grad-CAM ----------------------------------------------------

  function initGradcam(page) {
    var box = page.querySelector("[data-flare-cam]");
    if (!box) return;
    var heat = box.querySelector(".flare-cam-heat");
    var base = box.querySelector(".flare-cam-base");
    var slider = box.querySelector("[data-cam-opacity]");
    var noBase = box.querySelector("[data-cam-nobase]");
    slider.addEventListener("input", function () { heat.style.opacity = String(+slider.value / 100); });
    watch(heat);
    // flare-nrt keeps 131 Å images 4 days and alert maps 400: an older
    // alert's map is shown alone (it carries the solar limb).
    watch(base, function () { base.hidden = true; if (noBase) noBase.hidden = false; });
  }

  // ---- flare page: replays -----------------------------------------------------

  function fill(template, values) {
    return String(template).replace(/%\((\w+)\)s/g, function (_m, k) { return values[k] != null ? values[k] : ""; });
  }

  function initReplay(page) {
    var section = page.querySelector("#flare-replay");
    if (!section) return;
    var title = section.querySelector("[data-replay-title]");
    var note = section.querySelector("[data-replay-note]");
    var chartRoot = section.querySelector("[data-mount-later]");
    var i18n = JSON.parse(document.getElementById("fp-i18n").textContent);
    var stack = null;

    page.querySelectorAll("[data-replay]").forEach(function (btn) {
      btn.hidden = false;
      btn.addEventListener("click", function () {
        var p = player("flare-replay-player");
        if (!p) return;
        section.hidden = false;
        title.textContent = fill(i18n.replay_title, { when: btn.dataset.replayWhen });
        note.hidden = true;
        note.textContent = "";
        p.onFrame = function (f) { if (window.swxCharts && stack) window.swxCharts.cursor(stack, f.t); };
        p.load(btn.dataset.replay);
        if (window.swxCharts) stack = window.swxCharts.mount(chartRoot, btn.dataset.replayXray);
        section.focus({ preventScroll: true });
        section.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      });
    });

    section.addEventListener("swx:player-data", function (ev) {
      var d = ev.detail || {};
      if (d.unavailable) { note.textContent = i18n.unavailable; note.hidden = false; return; }
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

    section.querySelector("[data-replay-close]").addEventListener("click", function () {
      var p = player("flare-replay-player");
      if (p) p.reset();
      section.hidden = true;
    });
  }

  // ---- home: El Sol ahora -------------------------------------------------------

  function initSunNow() {
    var block = document.querySelector("[data-sun-now]");
    if (!block) return;
    var main = block.querySelector("[data-sun-main]");
    var caption = block.querySelector("[data-sun-wave]");
    // Service down and nothing in nginx's cache: the block goes away; the
    // rest of the home page doesn't depend on it.
    watch(main, function () {
      var p = player("sun-loop");
      if (p && p.frames.length) return;
      block.hidden = true;
    });
    block.querySelectorAll("[data-sun-thumb]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var p = player("sun-loop");
        if (p && (p.frames.length || p.root.hasAttribute("aria-busy"))) p.reset();
        var img = btn.querySelector("img");
        // Swap: the thumbnail becomes the large image and vice versa.
        var mainWave = main.dataset.wave, mainLarge = main.getAttribute("src"), mainThumb = main.dataset.thumb;
        main.setAttribute("src", btn.dataset.large);
        main.dataset.wave = btn.dataset.wave;
        main.dataset.thumb = img.getAttribute("src");
        main.alt = "SDO/AIA " + btn.dataset.wave + " Å";
        caption.textContent = btn.dataset.wave + " Å";
        btn.dataset.large = mainLarge;
        btn.dataset.wave = mainWave;
        img.setAttribute("src", mainThumb);
        img.alt = "SDO/AIA " + mainWave + " Å";
        btn.querySelector("span").textContent = mainWave + " Å";
        btn.setAttribute("aria-label", btn.dataset.label.replace("%s", mainWave));
      });
    });
    var loopRoot = document.getElementById("sun-loop");
    if (loopRoot) {
      // The loop is 171 Å: bring 171 back to the large image first.
      loopRoot.addEventListener("swx:player-start", function () {
        if (main.dataset.wave === "171") return;
        var back = block.querySelector('[data-sun-thumb][data-wave="171"]');
        if (back) back.click();
      });
    }
  }

  function init() {
    var page = document.querySelector(".flare-page");
    if (page) {
      initViewer(page);
      initGradcam(page);
      initReplay(page);
    }
    initSunNow();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

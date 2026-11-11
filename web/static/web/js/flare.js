/* The flare detector's page (D-101): the "imagen / diferencia" toggle and a
 * placeholder for an image the service can't give (a channel missing for
 * that slot, a difference without its baselines, a Grad-CAM not computed
 * yet). The images themselves come from nginx's cached image locations
 * (D-100); this file only swaps their URLs. No build step, no CDN (D-002):
 * plain ES2017, loaded deferred.
 */
(function () {
  "use strict";

  function markMissing(img) {
    var frame = img.closest(".flare-frame");
    if (!frame) return;
    var note = frame.querySelector(".flare-missing");
    frame.classList.add("is-missing");
    if (note) note.hidden = false;
  }

  function markLoaded(img) {
    var frame = img.closest(".flare-frame");
    if (!frame) return;
    var note = frame.querySelector(".flare-missing");
    frame.classList.remove("is-missing");
    if (note) note.hidden = true;
  }

  function watch(img) {
    img.addEventListener("error", function () { markMissing(img); });
    img.addEventListener("load", function () { markLoaded(img); });
    // An image that failed before this deferred script ran.
    if (img.complete && img.naturalWidth === 0 && img.getAttribute("src")) markMissing(img);
  }

  function init() {
    var page = document.querySelector(".flare-page");
    if (!page) return;
    page.querySelectorAll(".flare-frame img").forEach(watch);

    var toggle = page.querySelector("[data-flare-kind]");
    if (!toggle) return;
    toggle.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-kind]");
      if (!btn) return;
      var kind = btn.dataset.kind;
      toggle.querySelectorAll("[data-kind]").forEach(function (b) {
        var on = b === btn;
        b.classList.toggle("on", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      page.querySelectorAll("img[data-src-img]").forEach(function (img) {
        var next = kind === "diff" ? img.dataset.srcDiff : img.dataset.srcImg;
        if (img.getAttribute("src") !== next) img.setAttribute("src", next);
      });
      page.dataset.kind = kind;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

/* Theme toggle + mobile nav (12-HANDOFF-2026-10-15.md §2, D-053).
 * `data-theme` itself is set pre-paint by the inline script in base.html;
 * this file only wires the toggle button and the mobile menu once the DOM
 * is ready. No build step, no CDN (D-002): plain ES2017, loaded deferred.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    var sync = function () {
      themeBtn.setAttribute("aria-pressed", root.getAttribute("data-theme") === "night" ? "true" : "false");
    };
    sync();
    themeBtn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "night" ? "day" : "night";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      sync();
      // Instance 2's charts redraw on this (12-HANDOFF §2) instead of a
      // full page reload.
      document.dispatchEvent(new CustomEvent("swx:themechange", { detail: { theme: next } }));
    });
  }

  var menuBtn = document.getElementById("menu-toggle");
  var header = document.querySelector(".hdr");
  if (menuBtn && header) {
    menuBtn.addEventListener("click", function () {
      var open = header.getAttribute("data-open") === "true";
      header.setAttribute("data-open", open ? "false" : "true");
      menuBtn.setAttribute("aria-expanded", open ? "false" : "true");
    });
  }

  // The ES/EN links are plain links to the counterpart URL (D-053); a
  // section id is the same in both languages, so carry the #fragment over
  // (e.g. /datos-api/#hapi <-> /en/datos-api/#hapi, D-093).
  var langLinks = document.querySelectorAll(".lang-switch a");
  for (var i = 0; i < langLinks.length; i++) {
    langLinks[i].addEventListener("click", function () {
      this.setAttribute("href", this.getAttribute("href").split("#")[0] + location.hash);
    });
  }
})();

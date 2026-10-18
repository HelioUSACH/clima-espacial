/* Data access (v0.5.0 §1, §3): the product page's CSV dialog and the copy
 * buttons on the registration page. Plain JS, no build step (D-002).
 *
 * Every localStorage access is wrapped in try/catch: with storage blocked
 * (private mode, a privacy setting) the dialog still works, it just won't
 * remember the key. The key is sent as the X-API-Key header, so it never
 * lands in a URL, the browser history or a server log. */
(function () {
  "use strict";
  var STORE_KEY = "swx_api_key";

  function readKey() {
    try { return window.localStorage.getItem(STORE_KEY) || ""; } catch (e) { return ""; }
  }
  function writeKey(value) {
    try { if (value) { window.localStorage.setItem(STORE_KEY, value); } else { window.localStorage.removeItem(STORE_KEY); } } catch (e) { /* storage blocked: nothing to remember */ }
  }

  function isoDay(date) { return date.toISOString().slice(0, 10); }
  function isoTime(date) { return date.toISOString().replace(/\.\d{3}Z$/, "Z"); }

  function filenameFrom(response, fallback) {
    var header = response.headers.get("Content-Disposition") || "";
    var match = /filename="([^"]+)"/.exec(header);
    return match ? match[1] : fallback;
  }

  function setupDialog(dialog) {
    var form = dialog.querySelector("form");
    var error = dialog.querySelector(".csv-error");
    var custom = dialog.querySelector(".csv-custom");
    var keyInput = form.elements.key;
    var button = dialog.querySelector("[data-csv-download]");
    var opener = document.querySelector("[data-csv-open='" + dialog.id + "']");

    function show(message) { error.textContent = message; error.hidden = !message; }

    if (opener) {
      opener.addEventListener("click", function () {
        if (!keyInput.value) { keyInput.value = readKey(); }
        show("");
        if (typeof dialog.showModal === "function") { dialog.showModal(); } else { dialog.setAttribute("open", ""); }
      });
    }
    var today = new Date();
    form.elements.end.value = isoDay(today);
    form.elements.start.value = isoDay(new Date(today.getTime() - 7 * 86400000));
    Array.prototype.forEach.call(form.elements.range, function (radio) {
      radio.addEventListener("change", function () { custom.hidden = form.elements.range.value !== "custom"; });
    });

    function window_() {
      var choice = form.elements.range.value;
      var now = new Date();
      if (choice === "custom") {
        var start = new Date(form.elements.start.value + "T00:00:00Z");
        var stop = new Date(new Date(form.elements.end.value + "T00:00:00Z").getTime() + 86400000);
        if (isNaN(start) || isNaN(stop) || stop <= start) { return null; }
        return { start: start, stop: stop, keyless: false };
      }
      var days = Number(choice);
      return { start: new Date(now.getTime() - days * 86400000), stop: now, keyless: days === 1 };
    }

    button.addEventListener("click", function () {
      var range = window_();
      if (!range) { show(dialog.dataset.msgBadRange); return; }
      var key = keyInput.value.trim();
      if (!key && !range.keyless) { show(dialog.dataset.msgNeedKey); keyInput.focus(); return; }
      writeKey(form.elements.remember.checked ? key : "");
      var url = dialog.dataset.endpoint + "?start=" + encodeURIComponent(isoTime(range.start)) +
                "&stop=" + encodeURIComponent(isoTime(range.stop));
      var headers = key ? { "X-API-Key": key } : {};
      button.disabled = true;
      show(dialog.dataset.msgBusy);
      fetch(url, { headers: headers, credentials: "same-origin" }).then(function (response) {
        if (!response.ok) {
          return response.json().then(function (body) { throw new Error(body.error || dialog.dataset.msgFailed); },
                                      function () { throw new Error(dialog.dataset.msgFailed); });
        }
        var name = filenameFrom(response, dialog.dataset.product + ".csv");
        return response.blob().then(function (blob) {
          var link = document.createElement("a");
          link.href = URL.createObjectURL(blob);
          link.download = name;
          document.body.appendChild(link);
          link.click();
          setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
          show("");
          if (typeof dialog.close === "function") { dialog.close(); } else { dialog.removeAttribute("open"); }
        });
      }).catch(function (err) {
        show(err && err.message ? err.message : dialog.dataset.msgFailed);
      }).then(function () { button.disabled = false; });
    });
  }

  function setupCopy(buttonEl) {
    buttonEl.addEventListener("click", function () {
      var target = document.getElementById(buttonEl.getAttribute("data-copy"));
      if (!target) { return; }
      var text = target.value !== undefined && target.tagName === "INPUT" ? target.value : target.textContent;
      var done = function () {
        var label = buttonEl.textContent;
        buttonEl.textContent = buttonEl.getAttribute("data-copied") || label;
        setTimeout(function () { buttonEl.textContent = label; }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { if (target.select) { target.select(); } });
      } else if (target.select) {
        target.select();
      }
    });
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("dialog.csv-dialog"), setupDialog);
    Array.prototype.forEach.call(document.querySelectorAll("[data-copy]"), setupCopy);
  }
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); } else { init(); }
})();

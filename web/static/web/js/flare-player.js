/* The flare detector's frame player (D-102): the last 6 h of 171 Å (or its
 * difference) on the flare page and the home page, and an event's replay
 * with its Grad-CAM heat map on the flare page. One player per
 * [data-player] element; it plays inside the frame named by data-target.
 *
 * - Nothing is fetched until the start button is pressed (or, for a
 *   replay, until "ver repetición" is). Then the frame list comes from the
 *   page's own datos/*.json (public nginx paths, never the model's
 *   service), the images are preloaded with at most 4 requests in flight
 *   and a progress indicator, and playback starts once they are in.
 * - Controls: play/pause, step back/forward, a slider with each frame's
 *   T_OBS (UT and local time, localtime.js) and probability, speed
 *   5/10/20 fps, loop. Keyboard: every control is a native button, select,
 *   checkbox or range; Space/K plays or pauses and ←/→ step when the
 *   player has focus.
 * - prefers-reduced-motion: playback never starts by itself, not even
 *   after loading; the default speed is the slowest.
 *
 * No build step, no CDN (D-002): plain ES2017, loaded deferred.
 */
(function () {
  "use strict";

  var MAX_IN_FLIGHT = 4;
  var RETRY_MS = 800;
  var RETRY_LIST_MS = 1500;
  var reduced = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  function fill(template, values) {
    return String(template).replace(/%\((\w+)\)s/g, function (_m, k) { return values[k] != null ? values[k] : ""; });
  }

  function fmtProb(v, lang) {
    if (v == null) return "—";
    var s = v.toFixed(2);
    return lang === "es" ? s.replace(".", ",") : s;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Load urls (an array, null entries skipped) with at most MAX_IN_FLIGHT
  // requests at once, retrying a failure once (a 429 from a shared
  // address). onProgress(done, total); resolves to an array of booleans.
  function preload(urls, onProgress, token) {
    return new Promise(function (resolve) {
      var ok = urls.map(function () { return false; });
      var queue = [], done = 0, inFlight = 0;
      urls.forEach(function (u, i) { if (u) queue.push([i, 0]); });
      var total = queue.length;
      if (!total) { resolve(ok); return; }
      function finish() {
        done++;
        onProgress(done, total);
        if (done === total) resolve(ok);
        else pump();
      }
      function start(item) {
        inFlight++;
        var img = new Image();
        img.decoding = "async";
        img.onload = function () { inFlight--; if (token.cancelled) return; ok[item[0]] = true; finish(); };
        img.onerror = function () {
          inFlight--;
          if (token.cancelled) return;
          if (item[1] === 0) { setTimeout(function () { queue.push([item[0], 1]); pump(); }, RETRY_MS); return; }
          finish();
        };
        img.src = urls[item[0]];
      }
      function pump() {
        while (!token.cancelled && inFlight < MAX_IN_FLIGHT && queue.length) start(queue.shift());
      }
      pump();
    });
  }

  function Player(root) {
    this.root = root;
    this.i18n = JSON.parse(document.getElementById(root.dataset.i18nId).textContent);
    this.frameEl = document.getElementById(root.dataset.target);
    this.startBtn = root.querySelector("[data-fp-start]");
    this.progressEl = root.querySelector("[data-fp-progress]");
    this.barEl = root.querySelector("[data-fp-bar]");
    this.progressText = root.querySelector("[data-fp-progress-text]");
    this.controls = root.querySelector("[data-fp-controls]");
    this.playBtn = root.querySelector("[data-fp-play]");
    this.prevBtn = root.querySelector("[data-fp-prev]");
    this.nextBtn = root.querySelector("[data-fp-next]");
    this.slider = root.querySelector("[data-fp-slider]");
    this.marksEl = root.querySelector("[data-fp-marks]");
    this.labelEl = root.querySelector("[data-fp-label]");
    this.speedEl = root.querySelector("[data-fp-speed]");
    this.loopEl = root.querySelector("[data-fp-loop]");
    this.opacityEl = root.querySelector("[data-fp-opacity]");
    this.msgEl = root.querySelector("[data-fp-msg]");
    this.frames = [];
    this.index = 0;
    this.playing = false;
    this.token = { cancelled: false };
    this.onFrame = null;
    this.data = null;
    if (reduced && this.speedEl) this.speedEl.value = this.speedEl.options[0].value;
    root.__swxPlayer = this; // read by .github/scripts/screenshots.py and flare.js
    this.wire();
  }

  Player.prototype.layers = function () {
    if (!this.frameEl) return null;
    if (!this.imgEl) {
      this.imgEl = document.createElement("img");
      this.imgEl.className = "fp-img";
      this.imgEl.alt = "";
      this.imgEl.decoding = "async";
      this.heatEl = document.createElement("img");
      this.heatEl.className = "fp-heat";
      this.heatEl.alt = "";
      this.heatEl.hidden = true;
      this.frameEl.appendChild(this.imgEl);
      this.frameEl.appendChild(this.heatEl);
    }
    return this.imgEl;
  };

  Player.prototype.message = function (text) {
    if (!this.msgEl) return;
    this.msgEl.textContent = text || "";
    this.msgEl.hidden = !text;
  };

  Player.prototype.wire = function () {
    var self = this;
    if (this.startBtn) this.startBtn.addEventListener("click", function () { self.load(self.root.dataset.src); });
    if (this.playBtn) this.playBtn.addEventListener("click", function () { self.toggle(); });
    if (this.prevBtn) this.prevBtn.addEventListener("click", function () { self.pause(); self.step(-1); });
    if (this.nextBtn) this.nextBtn.addEventListener("click", function () { self.pause(); self.step(1); });
    if (this.slider) this.slider.addEventListener("input", function () { self.pause(); self.show(+self.slider.value); });
    if (this.opacityEl) {
      this.opacityEl.addEventListener("input", function () { self.applyOpacity(); });
    }
    this.root.addEventListener("keydown", function (ev) {
      if (!self.frames.length || ev.altKey || ev.ctrlKey || ev.metaKey) return;
      var tag = ev.target.tagName;
      var typing = tag === "SELECT" || (tag === "INPUT" && ev.target.type !== "range");
      // Space on a focused button is that button's own click; K always toggles.
      if (((ev.key === " " && tag !== "BUTTON") || ev.key === "k" || ev.key === "K") && !typing) {
        ev.preventDefault();
        self.toggle();
      } else if ((ev.key === "ArrowLeft" || ev.key === "ArrowRight") && tag !== "INPUT" && !typing) {
        ev.preventDefault();
        self.pause();
        self.step(ev.key === "ArrowLeft" ? -1 : 1);
      }
    });
  };

  Player.prototype.applyOpacity = function () {
    if (this.heatEl && this.opacityEl) this.heatEl.style.opacity = String(+this.opacityEl.value / 100);
  };

  // Stop everything and go back to the start button (the page shows its
  // still image again).
  Player.prototype.reset = function () {
    this.pause();
    this.token.cancelled = true;
    this.token = { cancelled: false };
    this.frames = [];
    this.data = null;
    if (this.frameEl) this.frameEl.classList.remove("is-playing");
    if (this.imgEl) { this.imgEl.removeAttribute("src"); this.heatEl.removeAttribute("src"); this.heatEl.hidden = true; }
    if (this.controls) this.controls.hidden = true;
    if (this.progressEl) this.progressEl.hidden = true;
    if (this.startBtn) this.startBtn.hidden = false;
    this.message("");
  };

  Player.prototype.progress = function (done, total) {
    if (this.barEl) this.barEl.style.width = (total ? (100 * done / total) : 0).toFixed(1) + "%";
    if (this.progressEl) this.progressEl.setAttribute("aria-valuenow", String(total ? Math.round(100 * done / total) : 0));
    if (this.progressText) this.progressText.textContent = fill(this.i18n.loading, { n: done, total: total });
  };

  // The frame list. "unavailable" can also mean another request was reading
  // the same thing from the service at that instant (the portal never makes
  // a request wait for another, D-100): ask once more, 1.5 s later.
  function fetchList(url, token, retried) {
    var bust = retried ? (url.indexOf("?") < 0 ? "?" : "&") + "retry=1" : "";
    return fetch(url + bust, { headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) {
        if (data && data.unavailable && !retried && !token.cancelled) {
          return new Promise(function (resolve) { setTimeout(resolve, RETRY_LIST_MS); })
            .then(function () { return fetchList(url, token, true); });
        }
        return data;
      });
  }

  Player.prototype.load = function (url) {
    var self = this;
    if (!url) return Promise.resolve();
    this.reset();
    var token = this.token;
    this.root.dispatchEvent(new CustomEvent("swx:player-start", { bubbles: true }));
    if (this.startBtn) this.startBtn.hidden = true;
    if (this.progressEl) this.progressEl.hidden = false;
    this.progress(0, 0);
    this.root.setAttribute("aria-busy", "true");
    return fetchList(url, token, false)
      .then(function (data) {
        if (token.cancelled) return null;
        self.data = data;
        self.root.dispatchEvent(new CustomEvent("swx:player-data", { detail: data, bubbles: true }));
        var frames = (data && data.frames) || [];
        if (data.unavailable || !frames.length) {
          self.finishLoading();
          if (self.progressEl) self.progressEl.hidden = true;
          if (self.startBtn) self.startBtn.hidden = false;
          self.message(data.unavailable ? (self.i18n.unavailable_short || self.i18n.error) : self.i18n.empty);
          return null;
        }
        var urls = [];
        frames.forEach(function (f) { urls.push(f.url); });
        frames.forEach(function (f) { urls.push(f.heat || null); });
        return preload(urls, function (d, t) { self.progress(d, t); }, token).then(function (ok) {
          if (token.cancelled) return;
          frames.forEach(function (f, i) { f.ok = ok[i]; f.heatOk = !!f.heat && ok[frames.length + i]; });
          self.ready(frames);
        });
      })
      .catch(function () {
        if (token.cancelled) return;
        self.finishLoading();
        if (self.progressEl) self.progressEl.hidden = true;
        if (self.startBtn) self.startBtn.hidden = false;
        self.message(self.i18n.error);
      });
  };

  Player.prototype.finishLoading = function () { this.root.removeAttribute("aria-busy"); };

  Player.prototype.ready = function (frames) {
    this.finishLoading();
    this.frames = frames;
    this.layers();
    if (this.progressEl) this.progressEl.hidden = true;
    if (this.controls) this.controls.hidden = false;
    this.slider.max = String(frames.length - 1);
    this.drawMarks();
    this.applyOpacity();
    this.frameEl.classList.add("is-playing");
    var marks = (this.data && this.data.marks) || {};
    // A replay opens on its peak; a loop on its newest frame.
    var first = marks.peak != null ? this.nearest(marks.peak) : frames.length - 1;
    if (!reduced) first = 0;
    this.show(first);
    if (!reduced) this.play();
  };

  Player.prototype.nearest = function (ts) {
    var best = 0, diff = Infinity;
    this.frames.forEach(function (f, i) {
      var d = Math.abs(f.t - ts);
      if (d < diff) { diff = d; best = i; }
    });
    return best;
  };

  Player.prototype.drawMarks = function () {
    if (!this.marksEl) return;
    this.marksEl.innerHTML = "";
    var marks = (this.data && this.data.marks) || {};
    var n = this.frames.length, self = this;
    ["start", "peak", "end"].forEach(function (key) {
      if (marks[key] == null || n < 2) return;
      var pos = 100 * self.nearest(marks[key]) / (n - 1);
      var el = document.createElement("span");
      el.className = "fp-mark fp-mark-" + key;
      el.style.left = pos.toFixed(2) + "%";
      el.innerHTML = "<i></i><b>" + escapeHtml(self.i18n.marks[key]) + "</b>";
      self.marksEl.appendChild(el);
    });
  };

  Player.prototype.markAt = function (i) {
    var marks = (this.data && this.data.marks) || {}, out = [], self = this;
    ["start", "peak", "end"].forEach(function (key) {
      if (marks[key] != null && self.nearest(marks[key]) === i) out.push(self.i18n.marks[key]);
    });
    return out;
  };

  Player.prototype.show = function (i) {
    var n = this.frames.length;
    if (!n) return;
    i = Math.max(0, Math.min(n - 1, i));
    this.index = i;
    var f = this.frames[i];
    if (f.ok) this.imgEl.src = f.url;
    if (f.heatOk) {
      this.heatEl.src = f.heat;
      this.heatEl.hidden = false;
    } else {
      this.heatEl.hidden = true;
    }
    this.frameEl.classList.toggle("is-missing-frame", !f.ok);
    this.slider.value = String(i);
    var when = f.obs != null ? f.obs : f.t;
    var timeParts = window.swxTime ? window.swxTime.parts(when, { date: true, seconds: f.obs != null }) : null;
    var timeText = timeParts ? timeParts.primary + (timeParts.secondary ? " (" + timeParts.secondary + ")" : "") : new Date(when * 1000).toISOString();
    var bits = [this.i18n.probability + " " + fmtProb(f.p, this.i18n.lang)];
    if (f.state && this.i18n.states[f.state]) bits.push(this.i18n.states[f.state]);
    this.markAt(i).forEach(function (m) { bits.push(m); });
    if (!f.ok) bits.push(this.i18n.missing_frame);
    var counter = fill(this.i18n.frame_of, { i: i + 1, n: n });
    this.labelEl.innerHTML = (timeParts && window.swxTime ? window.swxTime.html(when, { date: true, seconds: f.obs != null }) : escapeHtml(timeText)) +
      " · " + escapeHtml(bits.join(" · ")) + ' <span class="t-alt">· ' + escapeHtml(counter) + "</span>";
    this.slider.setAttribute("aria-valuetext", timeText + " · " + bits.join(" · "));
    if (this.onFrame) this.onFrame(f, i);
  };

  Player.prototype.step = function (delta) {
    var n = this.frames.length;
    if (!n) return;
    var next = this.index + delta;
    if (next >= n) next = 0;
    if (next < 0) next = n - 1;
    this.show(next);
  };

  Player.prototype.setPlayButton = function () {
    if (!this.playBtn) return;
    this.playBtn.setAttribute("aria-label", this.playing ? this.i18n.pause : this.i18n.play);
    this.playBtn.setAttribute("aria-pressed", this.playing ? "true" : "false");
    this.playBtn.classList.toggle("is-playing", this.playing);
  };

  Player.prototype.play = function () {
    if (!this.frames.length || this.playing) return;
    var self = this;
    this.playing = true;
    this.setPlayButton();
    var last = 0;
    if (this.index >= this.frames.length - 1 && !this.loopEl.checked) this.show(0);
    function tick(ts) {
      if (!self.playing) return;
      var interval = 1000 / (+self.speedEl.value || 10);
      if (!last) last = ts;
      if (ts - last >= interval) {
        last = ts;
        if (self.index >= self.frames.length - 1) {
          if (self.loopEl && self.loopEl.checked) self.show(0);
          else { self.pause(); return; }
        } else {
          self.show(self.index + 1);
        }
      }
      self.raf = requestAnimationFrame(tick);
    }
    this.raf = requestAnimationFrame(tick);
  };

  Player.prototype.pause = function () {
    this.playing = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.setPlayButton();
  };

  Player.prototype.toggle = function () {
    if (this.playing) this.pause();
    else this.play();
  };

  function init() {
    document.querySelectorAll("[data-player]").forEach(function (root) {
      if (!root.__swxPlayer) new Player(root);
    });
    document.addEventListener("swx:timechange", function () {
      document.querySelectorAll("[data-player]").forEach(function (root) {
        var p = root.__swxPlayer;
        if (p && p.frames.length) p.show(p.index);
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

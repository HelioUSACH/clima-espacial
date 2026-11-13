/* The flare detector's frame player (D-102): the last 6 h of 171 Å (or its
 * difference) on the flare page and the home page, and an event's replay
 * with its Grad-CAM heat map on the flare page. One player per
 * [data-player] element; it plays inside the frame named by data-target.
 *
 * - Nothing is fetched until the start button is pressed (or, for a
 *   replay, until "ver repetición" is). Then the frame list comes from the
 *   page's own datos/*.json (public nginx paths, never the model's
 *   service) and the images are preloaded in order, with at most 4
 *   requests in flight. Playback starts as soon as the first ~20 % of the
 *   frames are in (D-110) and the rest keep loading ahead of it: playback
 *   waits on a frame that isn't in yet rather than skip it. "cargando N/M"
 *   shows until every image is in.
 * - Never a blank frame (D-111): a frame is drawn only once it is decoded.
 *   The images and heat maps are two <canvas> layers; each frame is
 *   decoded off-screen (createImageBitmap, else img.decode()) and drawn
 *   only when ready, so a swap is one drawImage and the previous frame
 *   stays up until the next one is. Decoded frames are kept in a sliding
 *   window around the current one (phone: 8 behind, 24 ahead; desktop:
 *   16 behind, 64 ahead; ~1 MB each at 512 px) and decoded ahead while
 *   playing; the rest stay as the downloaded bytes. Playback waits for
 *   the next frame to be decoded rather than skip or blank it.
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
  var START_FRACTION = 0.2;
  var RETRY_MS = 800;
  var RETRY_LIST_MS = 1500;
  var MAX_DECODING = 3;
  // The decoded window (frames). A phone (narrow screen or <= 4 GB) keeps
  // ~33 MB of 512 px frames; a desktop ~81 MB.
  var small = Math.min(window.innerWidth || 9999, (window.screen && window.screen.width) || 9999) < 700 ||
    (navigator.deviceMemory != null && navigator.deviceMemory <= 4);
  var WINDOW = small ? { behind: 8, ahead: 24 } : { behind: 16, ahead: 64 };
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

  // Load urls (an array, null entries skipped), in order, with at most
  // MAX_IN_FLIGHT requests at once, retrying a failure once (a 429 from a
  // shared address). onProgress(done, total); onItem(index, ok, img) as
  // each one settles (img: the loaded, not yet decoded, Image); resolves
  // to an array of booleans.
  function preload(urls, onProgress, token, onItem) {
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
        img.onload = function () {
          inFlight--;
          if (token.cancelled) return;
          ok[item[0]] = true;
          if (onItem) onItem(item[0], true, img);
          finish();
        };
        img.onerror = function () {
          inFlight--;
          if (token.cancelled) return;
          if (item[1] === 0) { setTimeout(function () { queue.push([item[0], 1]); pump(); }, RETRY_MS); return; }
          if (onItem) onItem(item[0], false, null);
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

  // A loaded Image -> a decoded picture that draws without decoding again:
  // an ImageBitmap where the browser has one, else the Image itself once
  // its decode() has resolved. Resolves to {pic, bytes}.
  function decodeImage(img) {
    var bytes = (img.naturalWidth || 512) * (img.naturalHeight || 512) * 4;
    if (window.createImageBitmap) {
      return createImageBitmap(img).then(function (bm) { return { pic: bm, bytes: bytes }; },
        function () { return decodeElement(img, bytes); });
    }
    return decodeElement(img, bytes);
  }

  function decodeElement(img, bytes) {
    var p = img.decode ? img.decode() : Promise.resolve();
    return p.then(function () { return { pic: img, bytes: bytes }; });
  }

  function release(pic) { if (pic && pic.close) pic.close(); }

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
    this.loaded = false; // every image settled (read by screenshots.py)
    this.timing = null;  // {first, all}: ms from start to playback / to the last image
    this.token = { cancelled: false };
    this.onFrame = null;
    this.data = null;
    this.decoded = {};   // frame index -> {base, heat, bytes}
    this.decoding = {};  // frame index -> true while it decodes
    this.nDecoding = 0;
    this.painted = -1;   // the frame on the canvas, -1 for none
    // Read by .github/scripts/screenshots.py and loop_timing.py.
    this.stats = { bytes: 0, peakBytes: 0, frames: 0, peakFrames: 0, window: WINDOW };
    if (reduced && this.speedEl) this.speedEl.value = this.speedEl.options[0].value;
    root.__swxPlayer = this; // read by .github/scripts/screenshots.py and flare.js
    this.wire();
  }

  Player.prototype.layers = function () {
    // The page may have replaced the target frame (the flare page's event
    // panel is swapped per event): follow the id.
    var frame = document.getElementById(this.root.dataset.target);
    if (frame !== this.frameEl) { this.frameEl = frame; this.imgEl = null; this.heatEl = null; this.painted = -1; }
    if (!this.frameEl) return null;
    if (!this.imgEl || !this.frameEl.contains(this.imgEl)) {
      // Two canvases, drawn only with decoded frames (never an <img> whose
      // src changes: that blanks it while the new one decodes).
      this.imgEl = document.createElement("canvas");
      this.imgEl.className = "fp-img";
      this.imgEl.setAttribute("aria-hidden", "true");
      this.heatEl = document.createElement("canvas");
      this.heatEl.className = "fp-heat";
      this.heatEl.setAttribute("aria-hidden", "true");
      this.heatEl.hidden = true;
      this.painted = -1;
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
    this.loaded = false;
    this.timing = null;
    this.evict(function () { return false; });
    this.decoding = {};
    this.nDecoding = 0;
    this.painted = -1;
    if (this.frameEl) this.frameEl.classList.remove("is-playing", "is-missing-frame");
    if (this.imgEl) { clearCanvas(this.imgEl); clearCanvas(this.heatEl); this.heatEl.hidden = true; }
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
        // In frame order, each frame's heat map right after its image, so
        // the first frames are complete first.
        var urls = [], owner = [], left = [];
        frames.forEach(function (f, i) {
          f.ok = false;
          f.heatOk = false;
          f.settled = false;
          urls.push(f.url);
          owner.push([i, "ok"]);
          left.push(1);
          if (f.heat) { urls.push(f.heat); owner.push([i, "heatOk"]); left[i]++; }
        });
        var startAt = Math.max(1, Math.ceil(frames.length * START_FRACTION));
        var head = 0, started = false, t0 = performance.now();
        self.t0 = t0;
        function onItem(k, ok, img) {
          var o = owner[k], f = frames[o[0]];
          f[o[1]] = ok;
          if (ok) f[o[1] === "ok" ? "img" : "heatImg"] = img;
          if (--left[o[0]] > 0) return;
          f.settled = true;
          while (head < frames.length && frames[head].settled) head++;
          if (!started && head >= startAt) {
            started = true;
            self.ready(frames);
          } else if (started) {
            self.fillWindow();
            if (o[0] === self.index) self.show(self.index);
          }
        }
        return preload(urls, function (d, t) { self.progress(d, t); }, token, onItem).then(function () {
          if (token.cancelled) return;
          if (!started) self.ready(frames);
          self.timing = self.timing || { first: null, all: null };
          self.timing.all = Math.round(performance.now() - t0);
          self.loaded = true;
          self.finishLoading();
          if (self.progressEl) self.progressEl.hidden = true;
          self.root.dispatchEvent(new CustomEvent("swx:player-loaded", { detail: self.timing, bubbles: true }));
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

  // Enough frames are in to start (the rest keep loading; "cargando N/M"
  // stays until they are).
  Player.prototype.ready = function (frames) {
    this.frames = frames;
    this.layers();
    if (this.controls) this.controls.hidden = false;
    this.slider.max = String(frames.length - 1);
    this.drawMarks();
    this.applyOpacity();
    var marks = (this.data && this.data.marks) || {};
    // A replay opens on its peak; a loop on its newest frame.
    var first = marks.peak != null ? this.nearest(marks.peak) : frames.length - 1;
    if (!reduced) first = 0;
    this.autoplay = !reduced;
    this.show(first);
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
    // Drawn now if decoded; otherwise the current picture stays until it
    // is (fillWindow decodes this one first and paints it when ready).
    if (this.isReady(i)) this.paint(i);
    this.fillWindow();
    this.frameEl.classList.toggle("is-missing-frame", f.settled && !f.ok);
    this.slider.value = String(i);
    var when = f.obs != null ? f.obs : f.t;
    var timeParts = window.swxTime ? window.swxTime.parts(when, { date: true, seconds: f.obs != null }) : null;
    var timeText = timeParts ? timeParts.primary + (timeParts.secondary ? " (" + timeParts.secondary + ")" : "") : new Date(when * 1000).toISOString();
    var bits = [this.i18n.probability + " " + fmtProb(f.p, this.i18n.lang)];
    if (f.state && this.i18n.states[f.state]) bits.push(this.i18n.states[f.state]);
    this.markAt(i).forEach(function (m) { bits.push(m); });
    if (!f.settled || (f.ok && !this.isReady(i))) bits.push(this.i18n.pending_frame);
    else if (!f.ok) bits.push(this.i18n.missing_frame);
    var counter = fill(this.i18n.frame_of, { i: i + 1, n: n });
    this.labelEl.innerHTML = (timeParts && window.swxTime ? window.swxTime.html(when, { date: true, seconds: f.obs != null }) : escapeHtml(timeText)) +
      " · " + escapeHtml(bits.join(" · ")) + ' <span class="t-alt">· ' + escapeHtml(counter) + "</span>";
    this.slider.setAttribute("aria-valuetext", timeText + " · " + bits.join(" · "));
    if (this.onFrame) this.onFrame(f, i);
  };

  function clearCanvas(c) {
    var ctx = c && c.getContext("2d");
    if (ctx) ctx.clearRect(0, 0, c.width, c.height);
  }

  function draw(canvas, pic) {
    var w = pic.width || pic.naturalWidth, h = pic.height || pic.naturalHeight;
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    var ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(pic, 0, 0, w, h);
  }

  // A frame can be shown: settled, and (when it has them) its image and
  // heat map decoded. A frame whose image failed counts as ready: showing
  // it keeps the previous picture with the "imagen no disponible" note.
  Player.prototype.isReady = function (i) {
    var f = this.frames[i];
    if (!f || !f.settled) return false;
    if (!f.ok) return true;
    var d = this.decoded[i];
    return !!d && !!d.base && (!f.heatOk || !!d.heat);
  };

  Player.prototype.paint = function (i) {
    var f = this.frames[i], d = this.decoded[i];
    if (!this.layers()) return;
    if (f.ok && d) {
      // Both layers in the same task: the browser never shows one without
      // the other, and never an empty canvas.
      draw(this.imgEl, d.base);
      if (d.heat) { draw(this.heatEl, d.heat); this.heatEl.hidden = false; }
      else this.heatEl.hidden = true;
      this.painted = i;
    }
    if (!this.frameEl.classList.contains("is-playing") && this.painted >= 0) {
      this.frameEl.classList.add("is-playing");
      this.timing = this.timing || { first: Math.round(performance.now() - this.t0), all: null };
      if (!reduced && this.autoplay) { this.autoplay = false; this.play(); }
    }
  };

  // The indexes to keep decoded around the current frame, nearest first
  // (the current one, then ahead, then behind); wraps when looping.
  Player.prototype.windowIndexes = function () {
    var n = this.frames.length, out = [this.index], wrap = !this.loopEl || this.loopEl.checked, k, j;
    for (k = 1; k <= WINDOW.ahead && k < n; k++) {
      j = this.index + k;
      if (j >= n) { if (!wrap) break; j -= n; }
      out.push(j);
    }
    for (k = 1; k <= WINDOW.behind && k < n; k++) {
      j = this.index - k;
      if (j < 0) { if (!wrap) break; j += n; }
      out.push(j);
    }
    return out;
  };

  Player.prototype.evict = function (keep) {
    var self = this;
    Object.keys(this.decoded).forEach(function (k) {
      var i = +k;
      if (keep(i)) return;
      var d = self.decoded[k];
      release(d.base);
      release(d.heat);
      self.stats.bytes -= d.bytes;
      self.stats.frames--;
      delete self.decoded[k];
    });
  };

  // Keep the window decoded: drop what left it, decode what entered it,
  // MAX_DECODING at a time, nearest first.
  Player.prototype.fillWindow = function () {
    if (!this.frames.length) return;
    var want = this.windowIndexes(), inWin = {};
    want.forEach(function (i) { inWin[i] = true; });
    this.evict(function (i) { return inWin[i]; });
    for (var w = 0; w < want.length && this.nDecoding < MAX_DECODING; w++) {
      var i = want[w], f = this.frames[i];
      if (!f.settled || !f.ok || this.decoded[i] || this.decoding[i]) continue;
      this.decodeFrame(i);
    }
  };

  Player.prototype.decodeFrame = function (i) {
    var self = this, f = this.frames[i], token = this.token;
    this.decoding[i] = true;
    this.nDecoding++;
    var jobs = [decodeImage(f.img), f.heatOk && f.heatImg ? decodeImage(f.heatImg) : Promise.resolve(null)];
    Promise.all(jobs).then(function (r) {
      if (token.cancelled || self.frames[i] !== f) { release(r[0] && r[0].pic); release(r[1] && r[1].pic); return; }
      var d = { base: r[0].pic, heat: r[1] ? r[1].pic : null, bytes: r[0].bytes + (r[1] ? r[1].bytes : 0) };
      self.decoded[i] = d;
      self.stats.bytes += d.bytes;
      self.stats.frames++;
      if (self.stats.bytes > self.stats.peakBytes) self.stats.peakBytes = self.stats.bytes;
      if (self.stats.frames > self.stats.peakFrames) self.stats.peakFrames = self.stats.frames;
      if (i === self.index) self.show(i);
    }, function () {
      // Undecodable bytes: treat as a missing frame, never a blank one.
      if (!token.cancelled && self.frames[i] === f) { f.ok = false; if (i === self.index) self.show(i); }
    }).then(function () {
      if (token.cancelled) return;
      delete self.decoding[i];
      self.nDecoding--;
      self.fillWindow();
    });
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
        var atEnd = self.index >= self.frames.length - 1;
        if (atEnd && !(self.loopEl && self.loopEl.checked)) { self.pause(); return; }
        var next = atEnd ? 0 : self.index + 1;
        // Not in or not decoded yet: wait for it rather than skip or blank it.
        if (self.isReady(self.index) && self.isReady(next)) self.show(next);
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

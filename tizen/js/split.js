/* Split view: several live cameras at once, each on its own MSE player. */
(function () {
  "use strict";

  var MAX_TILES = 4;
  var STAGGER_MS = 1500;
  var MAX_RETRIES = 5;
  // Video arrives in 4-second fragments, so a normal buffer can exceed one fragment.
  var MAX_BEHIND_SECONDS = 9;
  var CATCH_UP_SECONDS = 4;
  var KEY_SELECTION = "blinkLive.splitSelection";

  var chipsEl = document.getElementById("spChips");
  var gridEl = document.getElementById("spGrid");
  var noteEl = document.getElementById("spNote");
  var toggleEl = document.getElementById("spToggle");

  var cameras = [];
  var selected = [];
  var tiles = {};
  var running = false;

  function stored(key) {
    try {
      return window.localStorage.getItem(key) || "";
    } catch (err) {
      return "";
    }
  }

  function store(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      /* The selection just will not be remembered. */
    }
  }

  var token = stored("blinkLive.token");

  function Tile(camera) {
    this.camera = camera;
    this.generation = 0;
    this.retries = 0;
    this.stopped = false;
    this.failed = false;
    this.ws = null;
    this.ms = null;
    this.sb = null;
    this.queue = [];
    this.objectUrl = "";
    this.retryTimer = 0;
    this.startTimer = 0;
    this.hasAudio = false;

    this.el = document.createElement("section");
    this.el.className = "sp-tile";
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
    var label = document.createElement("span");
    label.className = "sp-label";
    label.textContent = camera.display_name || camera.name;
    this.sound = document.createElement("button");
    this.sound.type = "button";
    this.sound.className = "sp-sound";
    this.sound.textContent = "SOUND OFF";
    this.sound.hidden = true;
    this.status = document.createElement("span");
    this.status.className = "sp-status";
    var tools = document.createElement("div");
    tools.className = "sp-tools";
    var viewTools = window.BlinkViewTools.create(this.el, this.video, function () {
      return camera;
    });
    tools.appendChild(viewTools.zoomButton);
    tools.appendChild(viewTools.snapButton);
    tools.appendChild(viewTools.fullButton);
    tools.appendChild(this.sound);
    this.el.appendChild(this.video);
    this.el.appendChild(label);
    this.el.appendChild(tools);
    this.el.appendChild(this.status);

    var self = this;
    this.sound.addEventListener("click", function () {
      setExclusiveSound(self);
    });
  }

  Tile.prototype.setStatus = function (text, bad) {
    this.status.textContent = text || "";
    this.status.classList.toggle("is-bad", !!bad);
  };

  Tile.prototype.connect = function () {
    var self = this;
    var generation = ++this.generation;
    var scheme = location.protocol === "https:" ? "wss://" : "ws://";
    var url = scheme + location.host + "/api/live/" + encodeURIComponent(this.camera.id) + "?audio_track=0&multi=1";
    if (token) {
      url += "&token=" + encodeURIComponent(token);
    }
    this.setStatus("Connecting…");
    var ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = function (event) {
      if (generation !== self.generation) {
        return;
      }
      if (typeof event.data === "string") {
        var message = null;
        try {
          message = JSON.parse(event.data);
        } catch (err) {
          return;
        }
        self.onMessage(message);
      } else {
        self.enqueue(new Uint8Array(event.data));
      }
    };
    ws.onclose = function () {
      if (generation === self.generation && !self.stopped && !self.failed) {
        self.scheduleRetry();
      }
    };
  };

  Tile.prototype.onMessage = function (message) {
    if (message.type === "status") {
      this.setStatus(message.message || "Waking camera…");
    } else if (message.type === "ready") {
      this.retries = 0;
      this.setStatus("Receiving picture…");
      this.setupMedia(message);
    } else if (message.type === "error") {
      this.setStatus(message.message || "The live view failed.", true);
      if (message.retryable === false) {
        this.failed = true;
      }
    } else if (message.type === "ended") {
      this.setStatus(message.message || "Reconnecting…");
      if (!message.retryable) {
        this.failed = true;
      }
    }
  };

  Tile.prototype.scheduleRetry = function () {
    var self = this;
    this.retries += 1;
    if (this.retries > MAX_RETRIES) {
      this.failed = true;
      this.setStatus("Could not keep this camera connected. Press STOP, then GO LIVE to retry.", true);
      return;
    }
    if (!this.status.classList.contains("is-bad")) {
      this.setStatus("Reconnecting…");
    }
    clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(function () {
      if (!self.stopped) {
        self.teardownMedia();
        self.connect();
      }
    }, 2000 * this.retries);
  };

  Tile.prototype.setupMedia = function (info) {
    var self = this;
    this.teardownMedia();
    if (!window.MediaSource) {
      this.fail("This browser does not support MediaSource.");
      return;
    }
    var video = info.video_codec || "avc1.42E01E";
    var mime = 'video/mp4; codecs="' + video + (info.audio_codec ? "," + info.audio_codec : "") + '"';
    this.hasAudio = !!info.audio_codec;
    if (!MediaSource.isTypeSupported(mime)) {
      mime = 'video/mp4; codecs="' + video + '"';
      this.hasAudio = false;
      if (!MediaSource.isTypeSupported(mime)) {
        this.fail("This browser cannot play " + video + ".");
        return;
      }
    }
    this.sound.hidden = !this.hasAudio;
    var ms = new MediaSource();
    this.ms = ms;
    this.objectUrl = URL.createObjectURL(ms);
    this.video.src = this.objectUrl;
    ms.addEventListener("sourceopen", function () {
      if (self.ms !== ms) {
        return;
      }
      try {
        var sb = ms.addSourceBuffer(mime);
        sb.mode = "segments";
        self.sb = sb;
        sb.addEventListener("updateend", function () {
          if (self.sb === sb) {
            self.pump();
          }
        });
        self.pump();
      } catch (err) {
        self.fail("Could not create the video buffer: " + err.message);
      }
    });
  };

  Tile.prototype.fail = function (text) {
    this.failed = true;
    this.setStatus(text, true);
    if (this.ws) {
      this.ws.close();
    }
  };

  Tile.prototype.restartStream = function () {
    this.queue = [];
    if (this.ws) {
      this.ws.close();
    }
  };

  Tile.prototype.enqueue = function (bytes) {
    if (this.queue.length > 260) {
      this.restartStream();
      return;
    }
    this.queue.push(bytes);
    this.pump();
  };

  /* Keep each tile near the live edge and its buffer small. */
  Tile.prototype.maintain = function () {
    var ranges = this.sb.buffered;
    if (!ranges.length) {
      return false;
    }
    var start = ranges.start(0);
    var end = ranges.end(ranges.length - 1);
    var now = this.video.currentTime;
    if (now < start) {
      this.video.currentTime = start;
      now = start;
    } else if (end - now > MAX_BEHIND_SECONDS) {
      this.video.currentTime = end - CATCH_UP_SECONDS;
      now = this.video.currentTime;
    }
    if (now - start > 30) {
      this.sb.remove(start, now - 10);
      return true;
    }
    return false;
  };

  Tile.prototype.pump = function () {
    var sb = this.sb;
    if (!sb || !this.ms || this.ms.readyState !== "open" || sb.updating) {
      return;
    }
    try {
      if (this.maintain()) {
        return;
      }
      if (!this.queue.length) {
        this.play();
        return;
      }
      var chunk = this.queue.shift();
      sb.appendBuffer(chunk);
      this.setStatus("");
    } catch (err) {
      this.restartStream();
    }
  };

  Tile.prototype.play = function () {
    if (this.video.paused && this.video.readyState >= 2) {
      var promise = this.video.play();
      if (promise && promise.catch) {
        promise.catch(function () {
          /* The next chunk retries playback. */
        });
      }
    }
  };

  Tile.prototype.teardownMedia = function () {
    this.sb = null;
    this.ms = null;
    this.queue = [];
    this.video.removeAttribute("src");
    this.video.load();
    if (this.objectUrl) {
      URL.revokeObjectURL(this.objectUrl);
      this.objectUrl = "";
    }
  };

  Tile.prototype.stop = function () {
    this.stopped = true;
    this.generation += 1;
    clearTimeout(this.retryTimer);
    clearTimeout(this.startTimer);
    if (this.ws) {
      this.ws.close();
    }
    this.teardownMedia();
    if (this.el.parentNode) {
      this.el.parentNode.removeChild(this.el);
    }
  };

  function setExclusiveSound(target) {
    var turningOn = target.video.muted;
    Object.keys(tiles).forEach(function (id) {
      var tile = tiles[id];
      var on = turningOn && tile === target;
      tile.video.muted = !on;
      tile.sound.textContent = on ? "SOUND ON" : "SOUND OFF";
      tile.sound.classList.toggle("is-on", on);
    });
  }

  function cameraById(id) {
    return cameras.filter(function (camera) {
      return camera.id === id;
    })[0];
  }

  function updateLayout() {
    gridEl.className = "sp-grid sp-" + Object.keys(tiles).length;
  }

  function syncTiles() {
    Object.keys(tiles).forEach(function (id) {
      if (!running || selected.indexOf(id) === -1) {
        tiles[id].stop();
        delete tiles[id];
      }
    });
    if (running) {
      var delay = 0;
      selected.forEach(function (id) {
        if (tiles[id]) {
          return;
        }
        var tile = new Tile(cameraById(id));
        tiles[id] = tile;
        gridEl.appendChild(tile.el);
        tile.setStatus("Waiting…");
        tile.startTimer = setTimeout(function () {
          tile.connect();
        }, delay);
        delay += STAGGER_MS;
      });
    }
    updateLayout();
  }

  function renderChips() {
    chipsEl.textContent = "";
    cameras.forEach(function (camera) {
      var chip = document.createElement("button");
      var on = selected.indexOf(camera.id) !== -1;
      chip.type = "button";
      chip.className = "sp-chip" + (on ? " is-on" : "");
      chip.textContent = camera.display_name || camera.name;
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      chip.disabled = !camera.online || (!on && selected.length >= MAX_TILES);
      chip.addEventListener("click", function () {
        var index = selected.indexOf(camera.id);
        if (index === -1) {
          selected.push(camera.id);
        } else {
          selected.splice(index, 1);
        }
        store(KEY_SELECTION, JSON.stringify(selected));
        renderChips();
        syncTiles();
        updateToggle();
      });
      chipsEl.appendChild(chip);
    });
  }

  function updateToggle() {
    toggleEl.disabled = !running && !selected.length;
    toggleEl.textContent = running ? "STOP" : "GO LIVE";
    toggleEl.classList.toggle("is-live", running);
    noteEl.textContent = running ? "" : "Live view wakes each selected camera.";
  }

  function loadSelection() {
    var saved = [];
    try {
      saved = JSON.parse(stored(KEY_SELECTION) || "[]");
    } catch (err) {
      saved = [];
    }
    var online = cameras.filter(function (camera) {
      return camera.online;
    }).map(function (camera) {
      return camera.id;
    });
    var valid = Array.isArray(saved) ? saved.filter(function (id) {
      return online.indexOf(id) !== -1;
    }) : [];
    selected = (valid.length ? valid : online).slice(0, MAX_TILES);
  }

  var guard = window.BlinkWatchGuard.create(function () {
    running = false;
    syncTiles();
    updateToggle();
    noteEl.textContent = "Stopped to save camera battery.";
  });

  toggleEl.addEventListener("click", function () {
    running = !running;
    if (running) {
      guard.start();
    } else {
      guard.stop();
    }
    syncTiles();
    updateToggle();
  });

  window.addEventListener("pagehide", function () {
    guard.stop();
    running = false;
    syncTiles();
  });

  fetch("/api/cameras", { headers: token ? { "X-Bridge-Token": token } : {} })
    .then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error(data.error || "HTTP " + response.status);
        }
        return data;
      });
    })
    .then(function (data) {
      cameras = data.cameras || [];
      loadSelection();
      renderChips();
      updateToggle();
    })
    .catch(function (err) {
      noteEl.textContent = "Could not load cameras: " + err.message;
    });
})();

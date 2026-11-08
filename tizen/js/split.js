/* Split view: several live cameras at once, each on its own MSE player. */
(function () {
  "use strict";

  var MAX_TILES = 4;
  var STAGGER_MS = 1500;
  var MAX_RETRIES = 5;
  var MAX_ZOOM = 8;
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
    this.zoomButton = toolButton("1.0x");
    this.zoomButton.title = "Reset zoom";
    this.zoomButton.hidden = true;
    this.snapButton = toolButton("SNAP");
    this.snapButton.title = "Save the current frame";
    this.snapButton.disabled = true;
    this.fullButton = toolButton("FULL");
    this.fullButton.title = "Fullscreen (or double-click the video)";
    this.status = document.createElement("span");
    this.status.className = "sp-status";
    var tools = document.createElement("div");
    tools.className = "sp-tools";
    tools.appendChild(this.zoomButton);
    tools.appendChild(this.snapButton);
    tools.appendChild(this.fullButton);
    tools.appendChild(this.sound);
    this.el.appendChild(this.video);
    this.el.appendChild(label);
    this.el.appendChild(tools);
    this.el.appendChild(this.status);

    this.scale = 1;
    this.panX = 0;
    this.panY = 0;
    this.snapTimer = 0;
    this.bindControls();
  }

  function toolButton(text) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "sp-tool";
    button.textContent = text;
    return button;
  }

  Tile.prototype.bindControls = function () {
    var self = this;
    var video = this.video;
    var drag = null;

    this.sound.addEventListener("click", function () {
      setExclusiveSound(self);
    });
    this.zoomButton.addEventListener("click", function () {
      self.resetZoom();
    });
    this.snapButton.addEventListener("click", function () {
      self.snapshot();
    });
    this.fullButton.addEventListener("click", function () {
      self.toggleFullscreen();
    });
    video.addEventListener("dblclick", function () {
      self.toggleFullscreen();
    });
    video.addEventListener("loadeddata", function () {
      self.snapButton.disabled = false;
    });
    this.el.addEventListener("fullscreenchange", function () {
      self.fullButton.textContent = document.fullscreenElement === self.el ? "EXIT" : "FULL";
      self.resetZoom();
    });

    this.el.addEventListener("wheel", function (event) {
      event.preventDefault();
      var rect = self.el.getBoundingClientRect();
      var pointerX = event.clientX - rect.left;
      var pointerY = event.clientY - rect.top;
      var next = Math.min(MAX_ZOOM, Math.max(1, self.scale * Math.exp(-event.deltaY * 0.002)));
      var ratio = next / self.scale;
      self.panX = pointerX - (pointerX - self.panX) * ratio;
      self.panY = pointerY - (pointerY - self.panY) * ratio;
      self.scale = next;
      self.applyZoom();
    }, { passive: false });

    video.addEventListener("pointerdown", function (event) {
      if (self.scale <= 1 || event.button !== 0) {
        return;
      }
      drag = { x: event.clientX - self.panX, y: event.clientY - self.panY };
      video.setPointerCapture(event.pointerId);
      self.el.classList.add("is-dragging");
    });
    video.addEventListener("pointermove", function (event) {
      if (drag) {
        self.panX = event.clientX - drag.x;
        self.panY = event.clientY - drag.y;
        self.applyZoom();
      }
    });
    function endDrag() {
      drag = null;
      self.el.classList.remove("is-dragging");
    }
    video.addEventListener("pointerup", endDrag);
    video.addEventListener("pointercancel", endDrag);
  };

  Tile.prototype.applyZoom = function () {
    var minX = this.el.clientWidth * (1 - this.scale);
    var minY = this.el.clientHeight * (1 - this.scale);
    this.panX = Math.min(0, Math.max(minX, this.panX));
    this.panY = Math.min(0, Math.max(minY, this.panY));
    var zoomed = this.scale > 1.001;
    this.video.style.transform = zoomed
      ? "translate(" + this.panX + "px," + this.panY + "px) scale(" + this.scale + ")"
      : "";
    this.zoomButton.hidden = !zoomed;
    this.zoomButton.textContent = this.scale.toFixed(1) + "x";
    this.el.classList.toggle("is-zoomed", zoomed);
  };

  Tile.prototype.resetZoom = function () {
    this.scale = 1;
    this.panX = 0;
    this.panY = 0;
    this.applyZoom();
  };

  Tile.prototype.toggleFullscreen = function () {
    if (document.fullscreenElement === this.el) {
      document.exitFullscreen();
    } else if (this.el.requestFullscreen) {
      this.el.requestFullscreen();
    }
  };

  /* Always saves the whole camera frame, whatever the current zoom. */
  Tile.prototype.snapshot = function () {
    var self = this;
    var video = this.video;
    if (!video.videoWidth) {
      return;
    }
    var canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(function (blob) {
      if (!blob) {
        return;
      }
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = snapshotName(self.camera);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(function () {
        URL.revokeObjectURL(url);
      }, 10000);
      self.snapButton.textContent = "SAVED";
      clearTimeout(self.snapTimer);
      self.snapTimer = setTimeout(function () {
        self.snapButton.textContent = "SNAP";
      }, 1200);
    }, "image/jpeg", 0.95);
  };

  function pad2(value) {
    return (value < 10 ? "0" : "") + value;
  }

  function snapshotName(camera) {
    var now = new Date();
    var name = String(camera.display_name || camera.name || "camera").replace(/[^a-z0-9]+/gi, "-");
    return name + "-" + now.getFullYear() + pad2(now.getMonth() + 1) + pad2(now.getDate()) +
      "-" + pad2(now.getHours()) + pad2(now.getMinutes()) + pad2(now.getSeconds()) + ".jpg";
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
    if (now < start || end - now > 4) {
      this.video.currentTime = Math.max(start, end - 0.5);
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
    this.snapButton.disabled = true;
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

  toggleEl.addEventListener("click", function () {
    running = !running;
    syncTiles();
    updateToggle();
  });

  window.addEventListener("pagehide", function () {
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

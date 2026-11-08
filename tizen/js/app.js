/* global BLINK_LIVE_CONFIG */
(function () {
  "use strict";

  // Tizen 5.5/6.0 (Samsung 2020-2021 TVs) ship Chromium 63-76, before flex
  // gap support landed. Keep the CSS-classic margins as a fallback.
  var userAgent = navigator.userAgent || "";
  var chromeMatch = /Chrome\/(\d+)/.exec(userAgent);
  var legacyFlex = false;
  if (chromeMatch && parseInt(chromeMatch[1], 10) < 84) {
    legacyFlex = true;
  } else if (/Tizen\s+[0-9]/i.test(userAgent) && !chromeMatch) {
    legacyFlex = true;
  }
  if (legacyFlex) {
    document.documentElement.className += " legacy-flex";
  }

  var DEFAULTS = window.BLINK_LIVE_CONFIG || {};
  var KEY_BRIDGE = "blinkLive.bridge";
  var KEY_TOKEN = "blinkLive.token";
  var KEY_AUDIO = "blinkLive.audioTrack";
  var MAX_LIVE_SECONDS = 285;
  var LIVE_LIMIT_LABEL = "05:00";

  var state = {
    activeView: "boot",
    health: null,
    cameras: [],
    selected: 0,
    viewerCamera: null,
    viewerIndex: 0,
    streamInfo: null,
    audioAvailable: false,
    audioOn: false,
    audioBlocked: false,
    connecting: false,
    playerMode: "mse",
    playerProtocol: "ts",
    tsFallback: false,
    avplayCameraId: null,
    avplaySessionId: null,
    avplayStarted: false,
    streamToken: 0,
    ws: null,
    ms: null,
    sb: null,
    mseToken: 0,
    queue: [],
    objectUrl: null,
    reconnectTimer: null,
    connectTimer: null,
    viewerTimer: null,
    streamStartedAt: 0,
    autoRetries: 0,
    closingViewer: false,
    settingsOpen: false,
    beforeSettings: "home",
    settingsAudio: 0,
    checking: false,
    checkSeq: 0,
    quietRefreshing: false,
    lastBack: 0,
    stateFocus: 0,
    toastTimer: null
  };

  var el = {};

  function $(id) {
    return document.getElementById(id);
  }

  function cacheElements() {
    el.app = $("app");
    el.home = $("home");
    el.viewer = $("viewer");
    el.boot = $("boot");
    el.offline = $("offline");
    el.link = $("link");
    el.settings = $("settings");
    el.toast = $("toast");

    el.clock = $("clock");
    el.date = $("date");
    el.bridgeState = $("bridgeState");
    el.bridgeAddress = $("bridgeAddress");
    el.channelGrid = $("channelGrid");
    el.channelCount = $("channelCount");
    el.heroNote = $("heroNote");
    el.sideUplink = $("sideUplink");
    el.sideBlink = $("sideBlink");
    el.sideStreams = $("sideStreams");
    el.footCameraCount = $("footCameraCount");
    el.footVersion = $("footVersion");

    el.bootAddress = $("bootAddress");
    el.bootMessage = $("bootMessage");
    el.bootRetry = $("bootRetry");
    el.bootSettings = $("bootSettings");
    el.bootVersion = $("bootVersion");

    el.offlineMessage = $("offlineMessage");
    el.offlineAddress = $("offlineAddress");
    el.offlineDetail = $("offlineDetail");
    el.offlineRetry = $("offlineRetry");
    el.offlineSettings = $("offlineSettings");

    el.setupUrl = $("setupUrl");
    el.linkEmail = $("linkEmail");
    el.linkDetail = $("linkDetail");
    el.linkRetry = $("linkRetry");
    el.linkSettings = $("linkSettings");

    el.avplaySurface = $("avplaySurface");
    el.liveVideo = $("liveVideo");
    el.viewerPoster = $("viewerPoster");
    el.viewerBack = $("viewerBack");
    el.viewerBackButton = $("viewerBackButton");
    el.viewerState = $("viewerState");
    el.viewerTimer = $("viewerTimer");
    el.viewerLoader = $("viewerLoader");
    el.loaderKicker = $("loaderKicker");
    el.loaderTitle = $("loaderTitle");
    el.loaderMessage = $("loaderMessage");
    el.viewerError = $("viewerError");
    el.viewerErrorMessage = $("viewerErrorMessage");
    el.retryStream = $("retryStream");
    el.closeError = $("closeError");
    el.viewerName = $("viewerName");
    el.viewerIndex = $("viewerIndex");
    el.viewerMeta = $("viewerMeta");
    el.audioButton = $("audioButton");
    el.audioLabel = $("audioLabel");
    el.audioMeter = $("audioMeter");

    el.settingBridge = $("settingBridge");
    el.settingToken = $("settingToken");
    el.settingsError = $("settingsError");
    el.settingsSave = $("settingsSave");
    el.settingsCancel = $("settingsCancel");
    el.settingsClose = $("settingsClose");
    el.audioSegments = document.querySelectorAll("[data-audio-track]");
  }

  function readStored(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  function writeStored(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      /* TV storage can be read-only or full; defaults still work. */
    }
  }

  function initialSettings() {
    var bridge = readStored(KEY_BRIDGE);
    if (bridge === null || bridge === "") {
      bridge = DEFAULTS.bridgeUrl || "";
    }
    var token = readStored(KEY_TOKEN);
    if (token === null) {
      token = DEFAULTS.apiToken || "";
    }
    var audio = parseInt(readStored(KEY_AUDIO), 10);
    if (!isFinite(audio)) {
      audio = parseInt(DEFAULTS.audioTrack, 10);
    }
    if (!isFinite(audio) || audio < 0 || audio > 1) {
      audio = 0;
    }
    return {
      bridge: normalizeBridge(bridge, true),
      token: token || "",
      audioTrack: audio
    };
  }

  var settings = initialSettings();

  function normalizeBridge(value, quiet) {
    var raw = String(value || "").trim();
    if (!raw) {
      return "";
    }
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
      raw = "http://" + raw;
    }
    try {
      var parsed = new URL(raw);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        throw new Error("Use http:// or https://.");
      }
      var path = parsed.pathname.replace(/\/+$/, "");
      return parsed.protocol + "//" + parsed.host + path;
    } catch (err) {
      if (!quiet) {
        throw new Error("That does not look like a valid bridge URL.");
      }
      return "";
    }
  }

  function bridgeHost() {
    if (!settings.bridge) {
      return "NO BRIDGE CONFIGURED";
    }
    try {
      return new URL(settings.bridge).host;
    } catch (err) {
      return settings.bridge;
    }
  }

  function setupUrl() {
    var url = settings.bridge ? settings.bridge + "/setup" : "";
    if (url && settings.token) {
      url += "?token=" + encodeURIComponent(settings.token);
    }
    return url;
  }

  function api(path, options) {
    options = options || {};
    if (!settings.bridge) {
      return Promise.reject(new Error("No bridge address is configured."));
    }
    var headers = { Accept: "application/json" };
    if (options.body) {
      headers["Content-Type"] = "application/json";
    }
    if (settings.token) {
      headers["X-Bridge-Token"] = settings.token;
    }
    var controller = null;
    var timer = null;
    var timeout = options.timeout || 14000;
    if (typeof AbortController !== "undefined") {
      controller = new AbortController();
      timer = window.setTimeout(function () {
        controller.abort();
      }, timeout);
    }
    return fetch(settings.bridge + path, {
      method: options.method || "GET",
      headers: headers,
      body: options.body,
      cache: "no-store",
      signal: controller ? controller.signal : undefined
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = {};
        try {
          data = text ? JSON.parse(text) : {};
        } catch (err) {
          data = { error: text || "HTTP " + response.status };
        }
        if (!response.ok) {
          var error = new Error(data.error || data.message || "HTTP " + response.status);
          error.status = response.status;
          error.data = data;
          throw error;
        }
        return data;
      });
    }).then(function (data) {
      if (timer) {
        window.clearTimeout(timer);
      }
      return data;
    }, function (err) {
      if (timer) {
        window.clearTimeout(timer);
      }
      if (err && err.name === "AbortError") {
        throw new Error("The bridge did not answer in time.");
      }
      throw err;
    });
  }

  /* ------------------------------------------------------------------ views */

  function showView(name) {
    var changed = state.activeView !== name;
    state.activeView = name;
    var views = {
      home: el.home,
      viewer: el.viewer,
      boot: el.boot,
      offline: el.offline,
      link: el.link
    };
    Object.keys(views).forEach(function (key) {
      var node = views[key];
      if (!node) {
        return;
      }
      if (key === name) {
        node.removeAttribute("hidden");
      } else {
        node.setAttribute("hidden", "hidden");
      }
    });
    if (el.app) {
      el.app.classList.toggle("is-booting", name === "boot");
    }
    if (changed && (name === "boot" || name === "offline" || name === "link")) {
      state.stateFocus = 0;
      window.setTimeout(function () {
        if (state.activeView === name) {
          focusStateControl(0, true);
        }
      }, 60);
    }
  }

  function stateControls() {
    if (state.activeView === "offline") {
      return [el.offlineRetry, el.offlineSettings];
    }
    if (state.activeView === "link") {
      return [el.linkRetry, el.linkSettings];
    }
    if (state.activeView === "boot") {
      return [el.bootRetry, el.bootSettings];
    }
    return [];
  }

  function focusStateControl(index, shouldFocus) {
    var controls = stateControls();
    if (!controls.length) {
      return;
    }
    state.stateFocus = (index + controls.length) % controls.length;
    for (var i = 0; i < controls.length; i += 1) {
      controls[i].classList.toggle("is-focused", i === state.stateFocus);
    }
    if (shouldFocus) {
      try {
        controls[state.stateFocus].focus({ preventScroll: true });
      } catch (err) {
        controls[state.stateFocus].focus();
      }
    }
  }

  function moveStateFocus(delta) {
    focusStateControl(state.stateFocus + delta, true);
  }

  function updateClock() {
    var now = new Date();
    var hh = String(now.getHours());
    var mm = String(now.getMinutes());
    if (hh.length < 2) {
      hh = "0" + hh;
    }
    if (mm.length < 2) {
      mm = "0" + mm;
    }
    if (el.clock) {
      el.clock.textContent = hh + ":" + mm;
    }
    if (el.date) {
      var days = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
      var months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
      el.date.textContent = days[now.getDay()] + " " + (now.getDate() < 10 ? "0" : "") + now.getDate() + " " + months[now.getMonth()];
    }
  }

  function kindLabel(kind) {
    if (kind === "doorbell") {
      return "DOORBELL";
    }
    if (kind === "outdoor") {
      // The hardware is an Outdoor 4, but the user may have placed it indoors.
      // Use the camera name for location and show the model in the viewer meta.
      return "CAMERA";
    }
    if (kind === "mini") {
      return "INDOOR";
    }
    return "CAMERA";
  }

  function cameraMeta(camera) {
    var parts = [kindLabel(camera.kind)];
    if (camera.model) {
      parts.push(String(camera.model).toUpperCase());
    }
    if (camera.online) {
      parts.push("ONLINE");
    } else {
      parts.push("OFFLINE");
    }
    if (camera.battery) {
      parts.push("BATTERY " + String(camera.battery).toUpperCase());
    }
    if (typeof camera.temperature_c === "number") {
      parts.push(camera.temperature_c + "°C");
    }
    return parts.join("  ·  ");
  }

  function viewerMeta(camera) {
    var parts = [kindLabel(camera.kind)];
    if (camera.model) {
      parts.push(String(camera.model).toUpperCase());
    }
    parts.push(camera.online ? "ONLINE" : "OFFLINE");
    if (camera.battery) {
      parts.push("BATTERY " + String(camera.battery).toUpperCase());
    }
    if (typeof camera.temperature_c === "number") {
      parts.push(camera.temperature_c + "°C");
    }
    if (state.streamInfo) {
      parts.push("AUDIO CH " + (state.streamInfo.audio_track + 1));
    }
    return parts.join("  ·  ");
  }

  function updateHeader(health) {
    var cfgHost = bridgeHost();
    if (el.bridgeAddress) {
      el.bridgeAddress.textContent = cfgHost;
    }
    if (el.footVersion && health && health.version) {
      el.footVersion.textContent = "v" + health.version;
    }
    if (el.bootVersion && health && health.version) {
      el.bootVersion.textContent = "v" + health.version;
    }

    if (!health) {
      setStatus(el.bridgeState, "BRIDGE CHECK", "wait");
      setSide(el.sideUplink, "CHECKING", "wait");
      setSide(el.sideBlink, "CHECKING", "wait");
      return;
    }

    var blink = health.blink || {};
    var liveCount = health.live ? Object.keys(health.live).length : 0;
    var stateName = blink.state || "unknown";

    setStatus(el.bridgeState, "BRIDGE ONLINE", "good");
    setSide(el.sideUplink, "LAN / ONLINE", "good");
    if (blink.ready) {
      setSide(el.sideBlink, "LINKED", "good");
    } else if (stateName === "starting" || stateName === "logging_in" || stateName === "2fa_pending") {
      setSide(el.sideBlink, "STARTING", "wait");
    } else {
      setSide(el.sideBlink, "NOT LINKED", "bad");
    }
    setSide(el.sideStreams, liveCount ? liveCount + " ACTIVE" : "STANDBY", liveCount ? "good" : "");
    if (el.footCameraCount) {
      var count = (health.blink && health.blink.camera_count) || state.cameras.length || 0;
      el.footCameraCount.textContent = count + (count === 1 ? " CAMERA" : " CAMERAS");
    }
  }

  function setStatus(node, text, tone) {
    if (!node) {
      return;
    }
    node.className = "bridge-state" + (tone === "bad" ? " is-bad" : tone === "wait" ? " is-wait" : "");
    node.innerHTML = "";
    var dot = document.createElement("i");
    dot.className = "status-dot";
    node.appendChild(dot);
    node.appendChild(document.createTextNode(text));
  }

  function setSide(node, text, tone) {
    if (!node) {
      return;
    }
    node.textContent = text;
    node.className = tone || "";
  }

  function setBoot(title, message) {
    if (el.loaderTitle) {
      /* Nothing here; viewer loader has its own title. */
    }
    if (el.bootMessage) {
      el.bootMessage.textContent = message || "Contacting the local relay.";
      if (settings.bridge) {
        var b = document.createElement("b");
        b.textContent = settings.bridge;
        el.bootMessage.appendChild(document.createTextNode(" "));
        el.bootMessage.appendChild(b);
      }
    }
    if (el.bootAddress) {
      el.bootAddress.textContent = bridgeHost();
    }
    if (title && el.bootMessage) {
      el.bootMessage.setAttribute("data-title", title);
    }
  }

  function showOffline(message, detail) {
    if (el.offlineMessage) {
      el.offlineMessage.textContent = message || "The TV could not reach the local bridge.";
    }
    if (el.offlineAddress) {
      el.offlineAddress.textContent = settings.bridge || "NO BRIDGE CONFIGURED";
    }
    if (el.offlineDetail) {
      el.offlineDetail.textContent = detail || "Check that the container is running and port 8099 is reachable from the TV.";
    }
    updateHeader(state.health);
    showView("offline");
  }

  function showLink(health) {
    var blink = (health && health.blink) || {};
    if (el.setupUrl) {
      el.setupUrl.textContent = setupUrl() || "Set a bridge address first.";
    }
    if (el.linkEmail) {
      el.linkEmail.textContent = blink.account || "your Blink email";
    }
    if (el.linkDetail) {
      el.linkDetail.textContent = blink.last_error
        ? blink.last_error
        : "Blink may occasionally require a new code. The bridge never stores your password.";
    }
    updateHeader(health);
    showView("link");
  }

  /* --------------------------------------------------------------- channels */

  function thumbUrl(camera) {
    var query = [];
    if (camera.thumbnail_rev) {
      query.push("v=" + encodeURIComponent(camera.thumbnail_rev));
    }
    if (settings.token) {
      query.push("token=" + encodeURIComponent(settings.token));
    }
    return settings.bridge + camera.thumbnail_path + (query.length ? "?" + query.join("&") : "");
  }

  function renderChannels() {
    if (!el.channelGrid) {
      return;
    }
    el.channelGrid.innerHTML = "";
    if (!state.cameras.length) {
      var empty = document.createElement("div");
      empty.className = "channel-skeleton";
      var text = document.createElement("span");
      text.textContent = "NO CAMERAS REPORTED — OPEN THE PAIRING PAGE AND RETRY";
      empty.appendChild(text);
      empty.appendChild(document.createElement("i"));
      el.channelGrid.appendChild(empty);
      return;
    }

    var fragment = document.createDocumentFragment();
    state.cameras.forEach(function (camera, index) {
      fragment.appendChild(buildChannel(camera, index));
    });
    el.channelGrid.appendChild(fragment);
    if (el.channelCount) {
      el.channelCount.textContent = state.cameras.length + (state.cameras.length === 1 ? " CHANNEL" : " CHANNELS");
    }
    if (el.footCameraCount) {
      el.footCameraCount.textContent = state.cameras.length + (state.cameras.length === 1 ? " CAMERA" : " CAMERAS");
    }
    var buttons = el.channelGrid.querySelectorAll(".channel");
    if (buttons.length) {
      focusChannel(Math.min(state.selected, buttons.length - 1), true);
      // Some TV engines clear focus while the first thumbnail decodes. Put the
      // remote's focus back on the selected channel once the paint settles.
      window.setTimeout(function () {
        if (state.activeView === "home" && !state.settingsOpen && document.activeElement === document.body) {
          focusChannel(state.selected, true);
        }
      }, 180);
    }
  }

  function buildChannel(camera, index) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "channel is-empty";
    button.setAttribute("data-index", String(index));
    button.setAttribute("aria-label", camera.display_name + ", " + kindLabel(camera.kind) + ", " + (camera.online ? "online" : "offline") + ". Press OK to watch live.");

    var frame = document.createElement("span");
    frame.className = "channel-frame";

    var img = document.createElement("img");
    img.className = "channel-thumb";
    img.alt = "";
    img.addEventListener("load", function () {
      button.classList.remove("is-empty");
    });
    img.addEventListener("error", function () {
      if (img.parentNode) {
        img.parentNode.removeChild(img);
      }
      button.classList.add("is-empty");
    });
    img.src = thumbUrl(camera);
    frame.appendChild(img);

    var noFeed = document.createElement("span");
    noFeed.className = "no-feed";
    var lens = document.createElement("i");
    var noFeedText = document.createElement("b");
    noFeedText.textContent = "NO RECENT FRAME";
    noFeed.appendChild(lens);
    noFeed.appendChild(noFeedText);
    frame.appendChild(noFeed);

    var indexNode = document.createElement("span");
    indexNode.className = "channel-index";
    indexNode.textContent = pad2(index + 1);
    frame.appendChild(indexNode);

    var stateNode = document.createElement("span");
    stateNode.className = "channel-state";
    if (!camera.online) {
      stateNode.className += " is-offline";
    } else if (camera.motion) {
      stateNode.className += " is-live";
    }
    var stateDot = document.createElement("i");
    stateDot.className = "status-dot";
    stateNode.appendChild(stateDot);
    stateNode.appendChild(document.createTextNode(camera.online ? (camera.motion ? "MOTION" : "STANDBY") : "OFFLINE"));
    frame.appendChild(stateNode);

    var quality = document.createElement("span");
    quality.className = "channel-quality";
    quality.textContent = "2K / H.264 + AAC";
    frame.appendChild(quality);

    var last = document.createElement("span");
    last.className = "channel-last";
    last.textContent = camera.thumbnail_rev ? "LATEST FRAME · CACHED" : "AWAITING FRAME";
    frame.appendChild(last);

    ["tl", "tr", "bl", "br"].forEach(function (corner) {
      var c = document.createElement("span");
      c.className = "focus-corner corner-" + corner;
      frame.appendChild(c);
    });

    button.appendChild(frame);

    var info = document.createElement("span");
    info.className = "channel-info";

    var nameRow = document.createElement("span");
    nameRow.className = "channel-name-row";
    var name = document.createElement("span");
    name.className = "channel-name";
    name.textContent = camera.display_name || camera.name || "CAMERA";
    var action = document.createElement("span");
    action.className = "channel-action";
    action.textContent = "PRESS OK TO GO LIVE →";
    nameRow.appendChild(name);
    nameRow.appendChild(action);
    info.appendChild(nameRow);

    var meta = document.createElement("span");
    meta.className = "channel-meta";
    meta.appendChild(document.createTextNode(kindLabel(camera.kind)));
    meta.appendChild(document.createTextNode("  ·  "));
    var onlineB = document.createElement("b");
    onlineB.textContent = camera.online ? "ONLINE" : "OFFLINE";
    if (!camera.online) {
      onlineB.className = "bad";
    }
    meta.appendChild(onlineB);
    if (camera.battery) {
      meta.appendChild(document.createTextNode("  ·  BATTERY "));
      var batteryB = document.createElement("b");
      batteryB.textContent = String(camera.battery).toUpperCase();
      if (String(camera.battery).toLowerCase() === "low") {
        batteryB.className = "warn";
      }
      meta.appendChild(batteryB);
    }
    if (typeof camera.temperature_c === "number") {
      meta.appendChild(document.createTextNode("  ·  " + camera.temperature_c + "°C"));
    }
    info.appendChild(meta);
    button.appendChild(info);

    button.addEventListener("focus", function () {
      setFocus(index, false);
    });
    button.addEventListener("click", function () {
      openViewer(index);
    });
    return button;
  }

  function pad2(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function focusChannel(index, shouldFocus) {
    var nodes = el.channelGrid.querySelectorAll(".channel");
    if (!nodes.length) {
      return;
    }
    state.selected = Math.max(0, Math.min(index, nodes.length - 1));
    for (var i = 0; i < nodes.length; i += 1) {
      nodes[i].classList.toggle("is-focused", i === state.selected);
    }
    if (shouldFocus) {
      try {
        nodes[state.selected].focus({ preventScroll: true });
      } catch (err) {
        nodes[state.selected].focus();
      }
    }
  }

  function setFocus(index, shouldFocus) {
    if (index === state.selected && !shouldFocus) {
      var nodes = el.channelGrid.querySelectorAll(".channel");
      for (var i = 0; i < nodes.length; i += 1) {
        nodes[i].classList.toggle("is-focused", i === index);
      }
      return;
    }
    focusChannel(index, shouldFocus);
  }

  function moveHomeFocus(delta) {
    if (!state.cameras.length) {
      return;
    }
    var count = state.cameras.length;
    var next = (state.selected + delta + count) % count;
    focusChannel(next, true);
    var selected = el.channelGrid.querySelectorAll(".channel")[state.selected];
    if (selected && selected.scrollIntoView) {
      selected.scrollIntoView({ block: "nearest" });
    }
  }

  /* --------------------------------------------------------------- bridge IO */

  function checkBridge(withBoot) {
    if (state.checking) {
      return;
    }
    if (!settings.bridge) {
      showOffline("No bridge address is configured.", "Press RED to enter the local bridge URL.");
      return;
    }
    state.checking = true;
    var seq = ++state.checkSeq;
    if (el.offlineAddress) {
      el.offlineAddress.textContent = settings.bridge;
    }
    if (withBoot) {
      setBoot("HANDSHAKE", "Contacting the local relay at");
      showView("boot");
    }
    api("/api/health").then(function (health) {
      if (seq !== state.checkSeq) {
        return null;
      }
      state.health = health;
      updateHeader(health);
      var blink = health.blink || {};
      if (blink.state === "starting" || blink.state === "logging_in") {
        setBoot("BLINK STARTING", "Restoring the saved Blink session at");
        showView("boot");
        window.setTimeout(function () {
          if (seq === state.checkSeq) {
            state.checking = false;
            checkBridge(false);
          }
        }, 2200);
        return null;
      }
      if (!blink.ready) {
        showLink(health);
        return null;
      }
      return api("/api/cameras").then(function (data) {
        if (seq !== state.checkSeq) {
          return;
        }
        state.cameras = data.cameras || [];
        renderChannels();
        showView("home");
        updateHeader(health);
        if (!state.cameras.length) {
          showToast("The bridge is linked, but Blink reported no cameras.");
        }
      }).catch(function (err) {
        if (seq !== state.checkSeq) {
          return;
        }
        if (err.status === 409) {
          showLink(err.data || health);
          return;
        }
        showOffline(err.message || "Could not load cameras.", "The bridge answered, but the camera list failed.");
      });
    }).catch(function (err) {
      if (seq !== state.checkSeq) {
        return;
      }
      state.health = null;
      updateHeader(null);
      showOffline(err.message || "The bridge did not answer.", "Check the Docker container, the port, and that this TV is on the LAN.");
    }).then(function () {
      if (seq === state.checkSeq) {
        state.checking = false;
      }
    });
  }

  function quietRefresh() {
    if (state.quietRefreshing || state.checking || state.settingsOpen || state.activeView !== "home") {
      return;
    }
    state.quietRefreshing = true;
    api("/api/health").then(function (health) {
      state.health = health;
      updateHeader(health);
      if (!health.blink || !health.blink.ready) {
        showLink(health);
        return null;
      }
      return api("/api/cameras").then(function (data) {
        var previous = state.selected;
        state.cameras = data.cameras || [];
        renderChannels();
        focusChannel(Math.min(previous, Math.max(0, state.cameras.length - 1)), true);
      });
    }).catch(function () {
      /* Keep the last good home screen; a later poll can recover. */
    }).then(function () {
      state.quietRefreshing = false;
    });
  }

  /* ------------------------------------------------------------------ viewer */

  function openViewer(index) {
    if (!state.cameras[index]) {
      return;
    }
    state.selected = index;
    state.viewerCamera = state.cameras[index];
    state.viewerIndex = index;
    state.autoRetries = 0;
    state.closingViewer = false;
    updateViewerChrome(state.viewerCamera);
    showView("viewer");
    loadViewerCamera();
  }

  function updateViewerChrome(camera) {
    if (el.viewerName) {
      el.viewerName.textContent = camera.display_name || camera.name || "CAMERA";
    }
    if (el.viewerIndex) {
      el.viewerIndex.textContent = pad2(state.viewerIndex + 1);
    }
    if (el.viewerMeta) {
      el.viewerMeta.textContent = viewerMeta(camera);
    }
    if (el.viewerTimer) {
      el.viewerTimer.textContent = "00:00 / " + LIVE_LIMIT_LABEL;
    }
  }

  function loadViewerCamera() {
    if (!state.viewerCamera) {
      return;
    }
    clearViewerTimers();
    stopAvPlay(false);
    closeStream();
    state.streamInfo = null;
    state.audioAvailable = false;
    state.audioOn = false;
    state.audioBlocked = false;
    state.streamStartedAt = 0;
    state.playerMode = hasAvPlay() ? "avplay" : "mse";
    state.playerProtocol = "ts";
    state.tsFallback = false;
    el.viewerError.hidden = true;
    el.viewerLoader.hidden = false;
    el.viewer.classList.remove("is-live");
    setViewerState("waking", "WAKING");
    setLoader("CHANNEL " + pad2(state.viewerIndex + 1) + " / HANDSHAKE", "WAKING CAMERA", "Blink is opening a secure relay. This can take a few seconds.");
    setViewerPoster(state.viewerCamera);
    updateAudioUI();
    updateViewerChrome(state.viewerCamera);
    if (state.playerMode === "avplay") {
      startAvPlay(false);
    } else {
      connectStream(false);
    }
  }

  function setViewerPoster(camera) {
    if (!el.viewerPoster) {
      return;
    }
    var poster = el.viewerPoster;
    poster.onload = function () {
      if (state.viewerCamera === camera) {
        el.viewer.classList.add("has-poster");
      }
    };
    poster.onerror = function () {
      el.viewer.classList.remove("has-poster");
    };
    poster.src = thumbUrl(camera);
  }

  function clearViewerPoster() {
    if (!el.viewerPoster) {
      return;
    }
    el.viewer.classList.remove("has-poster");
    el.viewerPoster.onload = null;
    el.viewerPoster.onerror = null;
    el.viewerPoster.removeAttribute("src");
  }

  function hasAvPlay() {
    return (
      typeof window.webapis !== "undefined" &&
      window.webapis &&
      window.webapis.avplay &&
      typeof window.webapis.avplay.open === "function"
    );
  }

  function startCurrentPlayer(isReconnect) {
    if (state.playerMode === "avplay") {
      startAvPlay(isReconnect);
    } else {
      connectStream(isReconnect);
    }
  }

  function connectStream(isReconnect) {
    if (!state.viewerCamera) {
      return;
    }
    var token = ++state.streamToken;
    if (!isReconnect) {
      state.autoRetries = 0;
    }
    closeWebSocket();
    destroyMediaSource();
    state.connecting = true;
    el.viewerError.hidden = true;
    el.viewerLoader.hidden = false;
    el.viewer.classList.remove("is-live");
    setViewerState("waking", isReconnect ? "RECONNECTING" : "WAKING");
    if (!isReconnect) {
      setLoader("CHANNEL " + pad2(state.viewerIndex + 1) + " / HANDSHAKE", "WAKING CAMERA", "Blink is opening a secure relay. This can take a few seconds.");
    } else {
      setLoader("CHANNEL " + pad2(state.viewerIndex + 1) + " / RECONNECT", "REOPENING THE EYE", "Blink ended the previous session. Starting a fresh live view.");
    }
    clearTimeout(state.connectTimer);
    state.connectTimer = window.setTimeout(function () {
      if (token === state.streamToken && state.activeView === "viewer") {
        showViewerError("The camera did not send a video keyframe in time.", true);
      }
    }, 52000);

    var path = state.viewerCamera.stream_path;
    var query = "?audio_track=" + settings.audioTrack;
    if (settings.token) {
      query += "&token=" + encodeURIComponent(settings.token);
    }
    var wsBase = settings.bridge.replace(/^http/i, "ws");
    var ws;
    try {
      ws = new WebSocket(wsBase + path + query);
    } catch (err) {
      showViewerError("Could not open the live socket: " + err.message, true);
      return;
    }
    state.ws = ws;
    ws.binaryType = "arraybuffer";
    ws.onopen = function () {
      if (token !== state.streamToken) {
        return;
      }
      setLoader("CHANNEL " + pad2(state.viewerIndex + 1) + " / CONNECTED", "ASKING THE CAMERA", "The relay is open. Waiting for the first video keyframe.");
    };
    ws.onmessage = function (event) {
      if (token !== state.streamToken) {
        return;
      }
      if (typeof event.data === "string") {
        var message;
        try {
          message = JSON.parse(event.data);
        } catch (err) {
          return;
        }
        handleServerMessage(message, token);
      } else if (event.data instanceof ArrayBuffer) {
        enqueueBytes(new Uint8Array(event.data), token);
      }
    };
    ws.onerror = function () {
      if (token === state.streamToken) {
        setLoader("CHANNEL " + pad2(state.viewerIndex + 1) + " / SOCKET", "RELAY INTERRUPTED", "The TV lost its WebSocket connection.");
      }
    };
    ws.onclose = function () {
      if (state.ws !== ws) {
        return;
      }
      state.ws = null;
      if (token !== state.streamToken || state.closingViewer) {
        return;
      }
      if (state.activeView === "viewer" && !el.viewerError.hidden) {
        return;
      }
      scheduleReconnect("The live relay closed unexpectedly.");
    };
  }

  function startAvPlay(isReconnect) {
    if (!state.viewerCamera || !hasAvPlay()) {
      return;
    }
    var camera = state.viewerCamera;
    var token = ++state.streamToken;
    if (!isReconnect) {
      state.autoRetries = 0;
    }
    stopAvPlay(false);
    closeWebSocket();
    destroyMediaSource();
    state.playerMode = "avplay";
    state.connecting = true;
    state.streamInfo = null;
    state.audioAvailable = false;
    state.audioOn = false;
    state.audioBlocked = false;
    el.viewer.classList.add("is-avplay");
    el.viewerError.hidden = true;
    el.viewerLoader.hidden = false;
    el.viewer.classList.remove("is-live");
    setViewerState("waking", isReconnect ? "RECONNECTING" : "WAKING");
    setLoader(
      "CHANNEL " + pad2(state.viewerIndex + 1) + " / AVPLAY",
      isReconnect ? "REOPENING THE EYE" : "WAKING CAMERA",
      "Opening the TV's native HLS decoder. This avoids the TV's MSE limitation."
    );
    updateAudioUI();
    var protocol = state.playerProtocol;
    var endpoint = protocol === "ts" ? "/ts" : "/hls";
    api("/api/live/" + encodeURIComponent(camera.id) + endpoint, {
      method: "POST",
      timeout: 75000,
      body: JSON.stringify({ audio_track: settings.audioTrack })
    }).then(function (data) {
      if (token !== state.streamToken || state.activeView !== "viewer") {
        return;
      }
      state.avplayCameraId = camera.id;
      state.avplaySessionId = data.session_id;
      state.streamStartedAt = Date.now();
      startViewerTimer();
      openAvPlay(data.stream_url || data.playlist_url, token);
    }).catch(function (err) {
      if (token !== state.streamToken) {
        return;
      }
      if (protocol === "ts" && !state.tsFallback) {
        state.tsFallback = true;
        state.playerProtocol = "hls";
        startAvPlay(true);
        return;
      }
      showViewerError(err.message || "Could not start the TV's live player.", true);
    });
  }

  function handleAvPlayFailure(message) {
    if (state.playerProtocol === "ts" && !state.tsFallback) {
      state.tsFallback = true;
      state.playerProtocol = "hls";
      stopAvPlay(false);
      startAvPlay(true);
      return;
    }
    showViewerError(message, true);
  }

  function openAvPlay(playlistUrl, token) {
    if (!hasAvPlay()) {
      showViewerError("AVPlay is not available on this TV.", false);
      return;
    }
    var player = window.webapis.avplay;
    var url = /^https?:\/\//i.test(playlistUrl)
      ? playlistUrl
      : settings.bridge + playlistUrl;
    try {
      player.stop();
    } catch (err) {
      /* NONE state */
    }
    try {
      player.close();
    } catch (err2) {
      /* NONE state */
    }
    try {
      player.open(url);
      player.setDisplayRect(0, 0, 1920, 1080);
      try {
        player.setDisplayMethod("PLAYER_DISPLAY_MODE_LETTER_BOX");
      } catch (displayErr) {
        /* older TV */
      }
      try {
        // The default AVPlay buffering target is deliberately conservative.
        // Two seconds is enough on a LAN and removes several seconds of black
        // screen before the doorbell picture appears.
        player.setBufferingParam(
          "PLAYER_BUFFER_FOR_PLAY",
          "PLAYER_BUFFER_SIZE_IN_SECOND",
          2
        );
        player.setBufferingParam(
          "PLAYER_BUFFER_FOR_RESUME",
          "PLAYER_BUFFER_SIZE_IN_SECOND",
          2
        );
      } catch (bufferErr) {
        /* older firmware */
      }
      try {
        if (
          window.webapis.productinfo &&
          window.webapis.productinfo.isUdPanelSupported()
        ) {
          // Do not let a 1080p default cap downscale a 2K camera on a UHD panel.
          player.setStreamingProperty(
            "ADAPTIVE_INFO",
            "FIXED_MAX_RESOLUTION=3840X2160"
          );
        }
      } catch (qualityErr) {
        /* optional property */
      }
      player.setListener({
        onbufferingstart: function () {
          if (token === state.streamToken) {
            setViewerState("waking", "BUFFERING");
          }
        },
        onbufferingprogress: function () {
          /* AVPlay does not expose a useful percentage here. */
        },
        onbufferingcomplete: function () {
          if (token === state.streamToken) {
            markAvPlayLive(token);
          }
        },
        oncurrentplaytime: function () {
          if (token === state.streamToken) {
            markAvPlayLive(token);
          }
        },
        onstreamcompleted: function () {
          if (token === state.streamToken) {
            scheduleReconnect("Blink ended the live view. Reconnecting…");
          }
        },
        onerror: function () {
          if (token === state.streamToken) {
            handleAvPlayFailure(
              "The TV's native player could not open the live transport."
            );
          }
        },
        onevent: function () {
          /* No-op; AVPlay emits several informational events. */
        }
      });
    } catch (openErr) {
      handleAvPlayFailure("AVPlay open failed: " + openErr.message);
      return;
    }
    player.prepareAsync(function () {
      if (token !== state.streamToken || state.activeView !== "viewer") {
        return;
      }
      state.avplayStarted = true;
      try {
        player.play();
        markAvPlayLive(token);
      } catch (playErr) {
        handleAvPlayFailure("AVPlay playback failed: " + playErr.message);
      }
    }, function () {
      if (token === state.streamToken) {
        handleAvPlayFailure("AVPlay could not prepare the live transport.");
      }
    });
  }

  function markAvPlayLive(token) {
    if (token !== state.streamToken || state.activeView !== "viewer") {
      return;
    }
    if (el.viewer.classList.contains("is-live")) {
      return;
    }
    state.connecting = false;
    state.audioAvailable = true;
    state.audioOn = true;
    state.autoRetries = 0;
    el.viewer.classList.remove("has-poster");
    el.viewer.classList.add("is-live");
    el.viewerError.hidden = true;
    setViewerState("live", "LIVE");
    updateAudioUI();
  }

  function stopAvPlay(notifyServer) {
    if (hasAvPlay()) {
      var player = window.webapis.avplay;
      try {
        player.stop();
      } catch (err) {
        /* not started */
      }
      try {
        player.close();
      } catch (err2) {
        /* already closed */
      }
    }
    state.avplayStarted = false;
    state.avplaySessionId = null;
    el.viewer.classList.remove("is-avplay");
    if (notifyServer && state.avplayCameraId) {
      var cameraId = state.avplayCameraId;
      state.avplayCameraId = null;
      postLiveStop(cameraId);
    }
  }

  function postLiveStop(cameraId) {
    var headers = { "Content-Type": "application/json" };
    if (settings.token) {
      headers["X-Bridge-Token"] = settings.token;
    }
    try {
      fetch(settings.bridge + "/api/live/" + encodeURIComponent(cameraId) + "/stop", {
        method: "POST",
        headers: headers,
        body: "{}",
        cache: "no-store"
      }).catch(function () {
        /* best-effort */
      });
    } catch (err) {
      /* best-effort */
    }
  }

  function handleServerMessage(message, token) {
    if (!message || token !== state.streamToken) {
      return;
    }
    if (message.type === "status") {
      if (message.state === "waking") {
        setLoader("CHANNEL " + pad2(state.viewerIndex + 1) + " / WAKE", "WAKING CAMERA", message.message || "The camera is waking up.");
        setViewerState("waking", "WAKING");
      }
      return;
    }
    if (message.type === "ready") {
      state.streamInfo = message;
      state.audioAvailable = !!message.audio_codec;
      state.audioOn = false;
      state.audioBlocked = false;
      state.connecting = false;
      clearTimeout(state.connectTimer);
      state.streamStartedAt = Date.now();
      startViewerTimer();
      setViewerState("waking", "FIRST FRAME");
      setLoader("CHANNEL " + pad2(state.viewerIndex + 1) + " / LIVE", "RECEIVING PICTURE", "Codec " + (message.video_codec || "H.264") + (message.audio_codec ? " + " + message.audio_codec : " (no audio track)") + ".");
      updateViewerChrome(state.viewerCamera);
      setupMediaSource(message, token);
      return;
    }
    if (message.type === "error") {
      clearTimeout(state.connectTimer);
      state.connecting = false;
      showViewerError(message.message || "The live view failed.", message.retryable !== false);
      return;
    }
    if (message.type === "ended") {
      clearTimeout(state.connectTimer);
      state.connecting = false;
      if (message.retryable) {
        scheduleReconnect(message.message || "Blink ended the live view.");
      } else {
        showViewerError(message.message || "The live view ended.", false);
      }
    }
  }

  function setupMediaSource(info, token) {
    destroyMediaSource();
    if (!window.MediaSource) {
      showViewerError("This TV does not expose MediaSource Extensions.", false);
      return;
    }
    var videoCodec = info.video_codec || "avc1.42E01E";
    var audioCodec = info.audio_codec || "";
    var mime = 'video/mp4; codecs="' + videoCodec + (audioCodec ? "," + audioCodec : "") + '"';
    var supported = false;
    try {
      supported = window.MediaSource.isTypeSupported(mime);
    } catch (err) {
      supported = false;
    }
    if (!supported && audioCodec) {
      mime = 'video/mp4; codecs="' + videoCodec + '"';
      try {
        supported = window.MediaSource.isTypeSupported(mime);
      } catch (err2) {
        supported = false;
      }
      if (supported) {
        state.audioAvailable = false;
        showToast("The TV does not support the camera audio codec. Playing video only.", true);
      }
    }
    if (!supported) {
      showViewerError("The TV does not support " + (info.video_codec || "this video codec") + ".", false);
      return;
    }

    var ms = new MediaSource();
    state.ms = ms;
    state.mseToken = token;
    state.queue = [];
    state.sb = null;
    state.objectUrl = URL.createObjectURL(ms);
    el.liveVideo.src = state.objectUrl;
    el.liveVideo.load();
    ms.addEventListener("sourceopen", function () {
      if (token !== state.streamToken || state.ms !== ms) {
        return;
      }
      try {
        var sb = ms.addSourceBuffer(mime);
        sb.mode = "segments";
        state.sb = sb;
        sb.addEventListener("updateend", function () {
          if (token === state.streamToken) {
            pumpQueue(token);
          }
        });
        pumpQueue(token);
      } catch (err) {
        showViewerError("Could not create the TV video buffer: " + err.message, false);
      }
    });
  }

  function enqueueBytes(bytes, token) {
    if (token !== state.streamToken || token !== state.mseToken) {
      return;
    }
    if (state.queue.length > 260) {
      showViewerError("The TV could not keep up with the live buffer.", true);
      return;
    }
    state.queue.push(bytes);
    pumpQueue(token);
  }

  function pumpQueue(token) {
    if (token !== state.streamToken || token !== state.mseToken) {
      return;
    }
    var sb = state.sb;
    var ms = state.ms;
    if (!sb || !ms || ms.readyState !== "open" || sb.updating) {
      return;
    }
    if (!state.queue.length) {
      maybeStartPlayback();
      return;
    }
    var chunk = state.queue.shift();
    try {
      sb.appendBuffer(chunk);
    } catch (err) {
      if (err && err.name === "QuotaExceededError") {
        try {
          var buffered = sb.buffered;
          if (buffered.length) {
            var end = buffered.end(buffered.length - 1);
            var start = Math.max(0, end - 8);
            sb.remove(0, start);
            state.queue.unshift(chunk);
            return;
          }
        } catch (removeErr) {
          /* fall through */
        }
      }
      showViewerError("The TV rejected the live video buffer: " + err.message, true);
    }
  }

  function maybeStartPlayback() {
    if (state.activeView !== "viewer" || !state.ms) {
      return;
    }
    if (!el.liveVideo.paused) {
      return;
    }
    if (state.audioAvailable && !state.audioBlocked) {
      el.liveVideo.muted = false;
      state.audioOn = true;
    } else {
      el.liveVideo.muted = true;
      state.audioOn = false;
    }
    var promise = el.liveVideo.play();
    if (promise && promise.catch) {
      promise.catch(function () {
        if (!state.audioAvailable) {
          updateAudioUI();
          return;
        }
        state.audioBlocked = true;
        state.audioOn = false;
        el.liveVideo.muted = true;
        updateAudioUI();
        showToast("Autoplay blocked. Press OK to enable camera sound.");
        var mutedPlay = el.liveVideo.play();
        if (mutedPlay && mutedPlay.catch) {
          mutedPlay.catch(function () {
            /* The first video frame will retry playback. */
          });
        }
      });
    }
    updateAudioUI();
  }

  function toggleAudio() {
    if (state.playerMode === "avplay") {
      showToast("AVPlay uses the TV's own volume. Use the TV remote.");
      return;
    }
    if (!state.audioAvailable) {
      showToast("This camera did not expose an audio track.", true);
      return;
    }
    if (el.liveVideo.muted) {
      el.liveVideo.muted = false;
      state.audioOn = true;
      state.audioBlocked = false;
      var promise = el.liveVideo.play();
      if (promise && promise.catch) {
        promise.catch(function () {
          showToast("The TV blocked audio. Press OK again.", true);
        });
      }
    } else {
      el.liveVideo.muted = true;
      state.audioOn = false;
    }
    updateAudioUI();
  }

  function updateAudioUI() {
    if (!el.audioButton) {
      return;
    }
    var avplay = state.playerMode === "avplay";
    var audible = avplay ? state.audioOn : state.audioOn && !el.liveVideo.muted;
    el.audioButton.classList.toggle("is-on", audible);
    el.audioButton.classList.toggle("is-off", !audible);
    if (el.audioMeter) {
      el.audioMeter.classList.toggle("is-on", audible);
    }
    if (!state.audioAvailable) {
      el.audioLabel.textContent = "NO AUDIO";
    } else if (avplay) {
      el.audioLabel.textContent = "TV AUDIO";
    } else if (audible) {
      el.audioLabel.textContent = "SOUND ON";
    } else {
      el.audioLabel.textContent = "SOUND OFF";
    }
  }

  function setViewerState(tone, label) {
    if (!el.viewerState) {
      return;
    }
    el.viewerState.className = "viewer-state" + (tone === "waking" ? " is-waking" : tone === "error" ? " is-error" : "");
    el.viewerState.innerHTML = "";
    var dot = document.createElement("i");
    dot.className = "status-dot";
    el.viewerState.appendChild(dot);
    el.viewerState.appendChild(document.createTextNode(label));
  }

  function setLoader(kicker, title, message) {
    if (el.loaderKicker) {
      el.loaderKicker.textContent = kicker;
    }
    if (el.loaderTitle) {
      el.loaderTitle.textContent = title;
    }
    if (el.loaderMessage) {
      el.loaderMessage.textContent = message;
    }
  }

  function startViewerTimer() {
    stopViewerTimer();
    updateViewerTimer();
    state.viewerTimer = window.setInterval(updateViewerTimer, 1000);
  }

  function updateViewerTimer() {
    if (!el.viewerTimer) {
      return;
    }
    if (!state.streamStartedAt) {
      el.viewerTimer.textContent = "00:00 / " + LIVE_LIMIT_LABEL;
      return;
    }
    var elapsed = Math.max(0, Math.floor((Date.now() - state.streamStartedAt) / 1000));
    if (elapsed > MAX_LIVE_SECONDS) {
      elapsed = MAX_LIVE_SECONDS;
    }
    el.viewerTimer.textContent = formatDuration(elapsed) + " / " + LIVE_LIMIT_LABEL;
  }

  function formatDuration(seconds) {
    var minutes = Math.floor(seconds / 60);
    var rest = seconds % 60;
    return pad2(minutes) + ":" + pad2(rest);
  }

  function stopViewerTimer() {
    if (state.viewerTimer) {
      window.clearInterval(state.viewerTimer);
      state.viewerTimer = null;
    }
  }

  function clearViewerTimers() {
    stopViewerTimer();
    clearTimeout(state.connectTimer);
    state.connectTimer = null;
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }

  function showViewerError(message, retryable) {
    if (!state.viewerCamera) {
      return;
    }
    clearViewerTimers();
    state.connecting = false;
    if (state.playerMode === "avplay") {
      stopAvPlay(false);
    }
    closeWebSocket();
    el.viewer.classList.remove("is-live");
    el.viewerLoader.hidden = true;
    el.viewerError.hidden = false;
    el.viewerErrorMessage.textContent = message || "The stream stopped.";
    el.retryStream.textContent = retryable === false ? "TRY AGAIN" : "RECONNECT";
    setViewerState("error", "NO SIGNAL");
    stopViewerTimer();
  }

  function scheduleReconnect(reason) {
    if (state.activeView !== "viewer" || state.closingViewer) {
      return;
    }
    state.autoRetries += 1;
    if (state.autoRetries > 4) {
      showViewerError(reason || "The live view could not be restored.", true);
      return;
    }
    if (state.playerMode === "avplay") {
      stopAvPlay(false);
    }
    closeStream();
    el.viewerError.hidden = true;
    el.viewerLoader.hidden = false;
    el.viewer.classList.remove("is-live");
    setViewerState("waking", "RECONNECTING");
    setLoader("CHANNEL " + pad2(state.viewerIndex + 1) + " / RECONNECT", "REOPENING THE EYE", reason || "The live view ended. Reconnecting to Blink.");
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = window.setTimeout(function () {
      if (state.activeView === "viewer" && !state.closingViewer) {
        startCurrentPlayer(true);
      }
    }, 1400);
  }

  function closeStream() {
    closeWebSocket();
    destroyMediaSource();
    state.connecting = false;
    stopViewerTimer();
  }

  function closeWebSocket() {
    if (state.ws) {
      var ws = state.ws;
      state.ws = null;
      try {
        if (ws.readyState === 0 || ws.readyState === 1) {
          ws.close(1000, "viewer closed");
        }
      } catch (err) {
        /* ignore */
      }
    }
  }

  function destroyMediaSource() {
    state.mseToken = 0;
    state.queue = [];
    if (el.liveVideo) {
      try {
        el.liveVideo.pause();
      } catch (err) {
        /* ignore */
      }
    }
    if (state.ms) {
      try {
        if (state.ms.readyState === "open" && state.sb) {
          state.ms.removeSourceBuffer(state.sb);
        }
      } catch (err) {
        /* ignore */
      }
    }
    state.sb = null;
    state.ms = null;
    if (state.objectUrl) {
      try {
        URL.revokeObjectURL(state.objectUrl);
      } catch (err) {
        /* ignore */
      }
      state.objectUrl = null;
    }
    if (el.liveVideo) {
      try {
        el.liveVideo.removeAttribute("src");
        el.liveVideo.load();
      } catch (err) {
        /* ignore */
      }
    }
    state.audioOn = false;
    state.audioBlocked = false;
    updateAudioUI();
  }

  function closeViewer() {
    state.closingViewer = true;
    state.streamToken += 1;
    clearViewerTimers();
    stopAvPlay(false);
    closeStream();
    clearViewerPoster();
    state.streamInfo = null;
    state.audioAvailable = false;
    state.audioOn = false;
    el.viewerError.hidden = true;
    el.viewerLoader.hidden = false;
    el.viewer.classList.remove("is-live");
    state.viewerCamera = null;
    showView("home");
    state.closingViewer = false;
    focusChannel(state.selected, true);
  }

  function switchViewerCamera(delta) {
    if (!state.cameras.length) {
      return;
    }
    var count = state.cameras.length;
    var next = (state.viewerIndex + delta + count) % count;
    state.viewerIndex = next;
    state.viewerCamera = state.cameras[next];
    state.autoRetries = 0;
    updateViewerChrome(state.viewerCamera);
    loadViewerCamera();
    showToast("CH " + pad2(next + 1) + " / " + (state.viewerCamera.display_name || state.viewerCamera.name));
  }

  /* ---------------------------------------------------------------- settings */

  function openSettings() {
    if (state.activeView === "viewer") {
      closeViewer();
    }
    state.beforeSettings = state.activeView;
    state.settingsOpen = true;
    state.settingsAudio = settings.audioTrack;
    el.settingBridge.value = settings.bridge;
    el.settingToken.value = settings.token;
    el.settingsError.hidden = true;
    el.settingsError.textContent = "";
    updateAudioSegments();
    el.settings.hidden = false;
    focusSettingsFirst();
  }

  function closeSettings() {
    state.settingsOpen = false;
    el.settings.hidden = true;
    if (state.activeView === "home") {
      focusChannel(state.selected, true);
    } else if (state.activeView === "offline" || state.activeView === "link" || state.activeView === "boot") {
      focusStateControl(0, true);
    }
  }

  function updateAudioSegments() {
    for (var i = 0; i < el.audioSegments.length; i += 1) {
      var node = el.audioSegments[i];
      node.classList.toggle("is-active", parseInt(node.getAttribute("data-audio-track"), 10) === state.settingsAudio);
    }
  }

  function focusSettingsFirst() {
    try {
      el.settingsSave.focus();
    } catch (err) {
      /* ignore */
    }
  }

  function settingsFocusables() {
    var nodes = el.settings.querySelectorAll("button, input, select, [tabindex]");
    var visible = [];
    for (var i = 0; i < nodes.length; i += 1) {
      if (!nodes[i].disabled && nodes[i].offsetParent !== null) {
        visible.push(nodes[i]);
      }
    }
    return visible;
  }

  function moveSettingsFocus(direction) {
    var nodes = settingsFocusables();
    if (!nodes.length) {
      return;
    }
    var active = document.activeElement;
    var index = nodes.indexOf(active);
    if (index < 0) {
      index = 0;
    } else {
      index = (index + direction + nodes.length) % nodes.length;
    }
    if (active && (active.tagName === "INPUT" || active.tagName === "SELECT")) {
      try {
        active.blur();
      } catch (err) {
        /* ignore */
      }
    }
    try {
      nodes[index].focus();
    } catch (err2) {
      /* ignore */
    }
  }

  function saveSettings() {
    var raw = el.settingBridge.value.trim();
    var normalized;
    try {
      normalized = normalizeBridge(raw, false);
    } catch (err) {
      el.settingsError.textContent = err.message;
      el.settingsError.hidden = false;
      return;
    }
    if (!normalized) {
      el.settingsError.textContent = "Enter the bridge URL, for example http://192.0.2.10:8099.";
      el.settingsError.hidden = false;
      return;
    }
    settings.bridge = normalized;
    settings.token = el.settingToken.value.trim();
    settings.audioTrack = state.settingsAudio;
    writeStored(KEY_BRIDGE, settings.bridge);
    writeStored(KEY_TOKEN, settings.token);
    writeStored(KEY_AUDIO, String(settings.audioTrack));
    state.health = null;
    state.cameras = [];
    closeSettings();
    checkBridge(true);
  }

  /* ------------------------------------------------------------------ toast */

  function showToast(message, isError) {
    if (!el.toast) {
      return;
    }
    clearTimeout(state.toastTimer);
    el.toast.textContent = message;
    el.toast.className = "toast is-visible" + (isError ? " is-error" : "");
    state.toastTimer = window.setTimeout(function () {
      el.toast.className = "toast";
    }, isError ? 5200 : 3200);
  }

  /* --------------------------------------------------------------- TV input */

  function isBackKey(key) {
    return key === 10009 || key === 27 || key === 461 || key === 10182;
  }

  function isPlayKey(key) {
    return key === 10252 || key === 415 || key === 19 || key === 179;
  }

  function isTextInput(node) {
    return !!node && (node.tagName === "INPUT" || node.tagName === "TEXTAREA" || node.tagName === "SELECT");
  }

  function handleSettingsKeys(event, key, back) {
    if (back) {
      event.preventDefault();
      closeSettings();
      return;
    }
    if (key === 38 || key === 40) {
      event.preventDefault();
      moveSettingsFocus(key === 40 ? 1 : -1);
      return;
    }
    if (key === 37 || key === 39) {
      var active = document.activeElement;
      if (active && active.getAttribute && active.hasAttribute("data-audio-track")) {
        event.preventDefault();
        state.settingsAudio = settings.audioTrack === 0 ? 1 : 0;
        updateAudioSegments();
        return;
      }
      event.preventDefault();
      moveSettingsFocus(key === 39 ? 1 : -1);
    }
  }

  function onKeyDown(event) {
    var key = event.keyCode || event.which;
    if (state.settingsOpen) {
      handleSettingsKeys(event, key, isBackKey(key));
      return;
    }
    if (isTextInput(event.target)) {
      if (isBackKey(key)) {
        event.preventDefault();
        closeSettings();
      }
      return;
    }

    if (state.activeView === "viewer") {
      if (isBackKey(key)) {
        event.preventDefault();
        closeViewer();
        return;
      }
      if (key === 13 || isPlayKey(key)) {
        event.preventDefault();
        if (!el.viewerError.hidden) {
          startCurrentPlayer(false);
        } else {
          toggleAudio();
        }
        return;
      }
      if (key === 37 || key === 39) {
        event.preventDefault();
        switchViewerCamera(key === 39 ? 1 : -1);
        return;
      }
      if (key === 403) {
        event.preventDefault();
        openSettings();
      }
      return;
    }

    if (state.activeView === "home") {
      if (key === 37 || key === 38) {
        event.preventDefault();
        moveHomeFocus(-1);
        return;
      }
      if (key === 39 || key === 40) {
        event.preventDefault();
        moveHomeFocus(1);
        return;
      }
      if (key === 13) {
        event.preventDefault();
        openViewer(state.selected);
        return;
      }
      if (key === 403) {
        event.preventDefault();
        openSettings();
        return;
      }
      if (isBackKey(key)) {
        event.preventDefault();
        requestExit();
      }
      return;
    }

    if (state.activeView === "offline" || state.activeView === "link" || state.activeView === "boot") {
      if (key === 37 || key === 38) {
        event.preventDefault();
        moveStateFocus(-1);
        return;
      }
      if (key === 39 || key === 40) {
        event.preventDefault();
        moveStateFocus(1);
        return;
      }
      if (key === 13) {
        event.preventDefault();
        var controls = stateControls();
        if (controls[state.stateFocus]) {
          controls[state.stateFocus].click();
        } else {
          checkBridge(true);
        }
        return;
      }
      if (key === 403) {
        event.preventDefault();
        openSettings();
        return;
      }
      if (isBackKey(key)) {
        event.preventDefault();
        requestExit();
      }
    }
  }

  function requestExit() {
    var now = Date.now();
    if (now - state.lastBack < 1800) {
      try {
        if (typeof tizen !== "undefined" && tizen.application) {
          tizen.application.getCurrentApplication().exit();
          return;
        }
      } catch (err) {
        /* fall through */
      }
      try {
        window.close();
      } catch (err2) {
        /* ignore */
      }
      return;
    }
    state.lastBack = now;
    showToast("Press BACK again to leave Blink / Live.");
  }

  function registerTvKeys() {
    if (typeof tizen === "undefined" || !tizen.tvinputdevice) {
      return;
    }
    var keys = ["ColorF0Red", "MediaPlayPause", "MediaPlay", "MediaPause"];
    var batchWorked = false;
    try {
      if (tizen.tvinputdevice.registerKeyBatch) {
        tizen.tvinputdevice.registerKeyBatch(keys);
        batchWorked = true;
      }
    } catch (err) {
      batchWorked = false;
    }
    if (!batchWorked) {
      for (var i = 0; i < keys.length; i += 1) {
        try {
          tizen.tvinputdevice.registerKey(keys[i]);
        } catch (keyErr) {
          /* Some panels do not expose media keys; arrows/OK/back still work. */
        }
      }
    }
  }

  /* -------------------------------------------------------------------- boot */

  function bindEvents() {
    window.addEventListener("keydown", onKeyDown, false);

    el.bootRetry.addEventListener("click", function () {
      checkBridge(true);
    });
    el.bootSettings.addEventListener("click", openSettings);
    el.offlineRetry.addEventListener("click", function () {
      checkBridge(true);
    });
    el.offlineSettings.addEventListener("click", openSettings);
    el.linkRetry.addEventListener("click", function () {
      checkBridge(true);
    });
    el.linkSettings.addEventListener("click", openSettings);

    el.viewerBack.addEventListener("click", closeViewer);
    el.viewerBackButton.addEventListener("click", closeViewer);
    el.audioButton.addEventListener("click", toggleAudio);
    el.retryStream.addEventListener("click", function () {
      startCurrentPlayer(false);
    });
    el.closeError.addEventListener("click", closeViewer);

    el.settingsSave.addEventListener("click", saveSettings);
    el.settingsCancel.addEventListener("click", closeSettings);
    el.settingsClose.addEventListener("click", closeSettings);
    var backdrop = el.settings.querySelector("[data-close-settings]");
    if (backdrop) {
      backdrop.addEventListener("click", closeSettings);
    }
    for (var i = 0; i < el.audioSegments.length; i += 1) {
      el.audioSegments[i].addEventListener("click", function (event) {
        var value = parseInt(event.currentTarget.getAttribute("data-audio-track"), 10);
        state.settingsAudio = value === 1 ? 1 : 0;
        updateAudioSegments();
        event.currentTarget.focus();
      });
    }

    el.liveVideo.addEventListener("playing", function () {
      if (state.activeView === "viewer" && state.ms) {
        el.viewer.classList.remove("has-poster");
        el.viewer.classList.add("is-live");
        el.viewerError.hidden = true;
        setViewerState("live", "LIVE");
        updateAudioUI();
      }
    });
    el.liveVideo.addEventListener("error", function () {
      if (state.activeView === "viewer" && state.ms) {
        showViewerError("The TV's video decoder rejected the live stream.", true);
      }
    });
    el.liveVideo.addEventListener("volumechange", updateAudioUI);

    document.addEventListener("visibilitychange", function () {
      if (document.hidden && state.activeView === "viewer") {
        closeViewer();
      }
    });
    window.addEventListener("beforeunload", function () {
      state.streamToken += 1;
      closeStream();
    });
    window.addEventListener("online", function () {
      if (state.activeView === "offline" || state.activeView === "boot") {
        checkBridge(true);
      }
    });
    window.addEventListener("offline", function () {
      if (state.activeView !== "viewer") {
        showOffline("This TV is offline.", "Reconnect the TV to the same LAN as the bridge.");
      }
    });
    window.addEventListener("error", function () {
      /* Keep the UI alive; diagnostics are in the remote console. */
    });
  }

  function init() {
    cacheElements();
    updateClock();
    window.setInterval(updateClock, 1000);
    registerTvKeys();
    bindEvents();
    updateHeader(null);
    showView("boot");
    checkBridge(true);
    window.setInterval(quietRefresh, 45000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

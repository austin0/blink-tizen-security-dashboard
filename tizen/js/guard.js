/* Stops a forgotten browser live view from keeping battery cameras awake. */
(function () {
  "use strict";

  var KEY_MINUTES = "blinkLive.guardMinutes";
  var KEY_OFF = "blinkLive.guardOff";
  var DEFAULT_MINUTES = 10;
  var PROMPT_SECONDS = 60;

  function isEnabled() {
    try {
      return window.localStorage.getItem(KEY_OFF) !== "1";
    } catch (err) {
      return true;
    }
  }

  function setEnabled(enabled) {
    try {
      if (enabled) {
        window.localStorage.removeItem(KEY_OFF);
      } else {
        window.localStorage.setItem(KEY_OFF, "1");
      }
    } catch (err) {
      /* The prompt simply stays on. */
    }
  }

  function watchMinutes() {
    var value = NaN;
    try {
      value = parseFloat(window.localStorage.getItem(KEY_MINUTES));
    } catch (err) {
      /* Use the default. */
    }
    return value > 0 ? value : DEFAULT_MINUTES;
  }

  function create(onExpire) {
    var watchTimer = 0;
    var countdownTimer = 0;
    var overlay = null;

    function hidePrompt() {
      clearInterval(countdownTimer);
      if (overlay && overlay.parentNode) {
        overlay.parentNode.removeChild(overlay);
      }
      overlay = null;
    }

    function stop() {
      clearTimeout(watchTimer);
      hidePrompt();
    }

    function expire() {
      stop();
      onExpire();
    }

    function start() {
      stop();
      if (isEnabled()) {
        watchTimer = setTimeout(showPrompt, watchMinutes() * 60000);
      }
    }

    function button(text, className, onClick) {
      var node = document.createElement("button");
      node.type = "button";
      node.className = className;
      node.textContent = text;
      node.addEventListener("click", onClick);
      return node;
    }

    function showPrompt() {
      // It may have been switched off in another tab since this timer started.
      if (!isEnabled()) {
        return;
      }
      var seconds = Math.min(PROMPT_SECONDS, Math.ceil(watchMinutes() * 60));
      // A deadline keeps this accurate if a hidden tab's timers are throttled.
      var deadline = Date.now() + seconds * 1000;
      overlay = document.createElement("div");
      overlay.className = "gd-overlay";
      overlay.setAttribute("role", "alertdialog");
      var panel = document.createElement("div");
      panel.className = "gd-panel";
      var title = document.createElement("h2");
      title.textContent = "Still watching?";
      var message = document.createElement("p");
      var actions = document.createElement("div");
      actions.className = "gd-actions";
      actions.appendChild(button("KEEP WATCHING", "sp-btn sp-primary", start));
      actions.appendChild(button("STOP NOW", "sp-btn", expire));
      panel.appendChild(title);
      panel.appendChild(message);
      panel.appendChild(actions);
      overlay.appendChild(panel);
      (document.fullscreenElement || document.body).appendChild(overlay);

      function tick() {
        var left = Math.ceil((deadline - Date.now()) / 1000);
        if (left <= 0) {
          expire();
          return;
        }
        message.textContent = "Live view stops in " + left + "s to save camera battery.";
      }
      tick();
      countdownTimer = setInterval(tick, 1000);
    }

    return { start: start, stop: stop };
  }

  window.BlinkWatchGuard = { create: create, isEnabled: isEnabled, setEnabled: setEnabled };
})();

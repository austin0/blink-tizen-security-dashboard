/* Zoom, pan, fullscreen and snapshots for a browser video view. */
(function () {
  "use strict";

  var MAX_ZOOM = 8;

  function toolButton(text, title) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "sp-tool";
    button.textContent = text;
    if (title) {
      button.title = title;
    }
    return button;
  }

  function pad2(value) {
    return (value < 10 ? "0" : "") + value;
  }

  function snapshotName(camera) {
    var now = new Date();
    var name = String((camera && (camera.display_name || camera.name)) || "camera").replace(/[^a-z0-9]+/gi, "-");
    return name + "-" + now.getFullYear() + pad2(now.getMonth() + 1) + pad2(now.getDate()) +
      "-" + pad2(now.getHours()) + pad2(now.getMinutes()) + pad2(now.getSeconds()) + ".jpg";
  }

  function isControl(target) {
    return !!(target && target.closest && target.closest("button, a, input"));
  }

  function create(container, video, getCamera) {
    var scale = 1;
    var panX = 0;
    var panY = 0;
    var drag = null;
    var snapTimer = 0;

    var zoomButton = toolButton("1.0x", "Reset zoom");
    var snapButton = toolButton("SNAP", "Save the current frame");
    var fullButton = toolButton("FULL", "Fullscreen (or double-click the video)");
    zoomButton.hidden = true;
    snapButton.disabled = true;
    video.style.transformOrigin = "0 0";

    function applyZoom() {
      var minX = container.clientWidth * (1 - scale);
      var minY = container.clientHeight * (1 - scale);
      panX = Math.min(0, Math.max(minX, panX));
      panY = Math.min(0, Math.max(minY, panY));
      var zoomed = scale > 1.001;
      video.style.transform = zoomed ? "translate(" + panX + "px," + panY + "px) scale(" + scale + ")" : "";
      zoomButton.hidden = !zoomed;
      zoomButton.textContent = scale.toFixed(1) + "x";
      container.classList.toggle("is-zoomed", zoomed);
    }

    function reset() {
      scale = 1;
      panX = 0;
      panY = 0;
      applyZoom();
    }

    function toggleFullscreen() {
      if (document.fullscreenElement === container) {
        document.exitFullscreen();
      } else if (container.requestFullscreen) {
        container.requestFullscreen();
      }
    }

    /* Always saves the whole camera frame, whatever the current zoom. */
    function snapshot() {
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
        link.download = snapshotName(getCamera());
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function () {
          URL.revokeObjectURL(url);
        }, 10000);
        snapButton.textContent = "SAVED";
        clearTimeout(snapTimer);
        snapTimer = setTimeout(function () {
          snapButton.textContent = "SNAP";
        }, 1200);
      }, "image/jpeg", 0.95);
    }

    zoomButton.addEventListener("click", reset);
    snapButton.addEventListener("click", snapshot);
    fullButton.addEventListener("click", toggleFullscreen);
    video.addEventListener("loadeddata", function () {
      snapButton.disabled = false;
    });
    video.addEventListener("emptied", function () {
      snapButton.disabled = true;
    });
    container.addEventListener("dblclick", function (event) {
      if (!isControl(event.target) && video.videoWidth) {
        toggleFullscreen();
      }
    });
    container.addEventListener("fullscreenchange", function () {
      fullButton.textContent = document.fullscreenElement === container ? "EXIT" : "FULL";
      reset();
    });

    container.addEventListener("wheel", function (event) {
      if (!video.videoWidth) {
        return;
      }
      event.preventDefault();
      var rect = container.getBoundingClientRect();
      var pointerX = event.clientX - rect.left;
      var pointerY = event.clientY - rect.top;
      var next = Math.min(MAX_ZOOM, Math.max(1, scale * Math.exp(-event.deltaY * 0.002)));
      var ratio = next / scale;
      panX = pointerX - (pointerX - panX) * ratio;
      panY = pointerY - (pointerY - panY) * ratio;
      scale = next;
      applyZoom();
    }, { passive: false });

    container.addEventListener("pointerdown", function (event) {
      if (scale <= 1 || event.button !== 0 || isControl(event.target)) {
        return;
      }
      drag = { x: event.clientX - panX, y: event.clientY - panY };
      container.setPointerCapture(event.pointerId);
      container.classList.add("is-dragging");
    });
    container.addEventListener("pointermove", function (event) {
      if (drag) {
        panX = event.clientX - drag.x;
        panY = event.clientY - drag.y;
        applyZoom();
      }
    });
    function endDrag() {
      drag = null;
      container.classList.remove("is-dragging");
    }
    container.addEventListener("pointerup", endDrag);
    container.addEventListener("pointercancel", endDrag);

    return {
      zoomButton: zoomButton,
      snapButton: snapButton,
      fullButton: fullButton,
      reset: reset
    };
  }

  window.BlinkViewTools = { create: create };
})();

/* Per-camera options page. */
(function () {
  "use strict";

  var camerasEl = document.getElementById("opCameras");
  var formEl = document.getElementById("opForm");
  var noteEl = document.getElementById("opNote");
  var applyEl = document.getElementById("opApply");
  var statusEl = document.getElementById("opStatus");

  var token = "";
  try {
    token = window.localStorage.getItem("blinkLive.token") || "";
  } catch (err) {
    token = "";
  }

  var cameras = [];
  var selectedId = "";
  var fields = [];
  var loadSeq = 0;

  function request(path, options) {
    options = options || {};
    var headers = { Accept: "application/json" };
    if (token) {
      headers["X-Bridge-Token"] = token;
    }
    if (options.body) {
      headers["Content-Type"] = "application/json";
    }
    return fetch(path, { method: options.method || "GET", headers: headers, body: options.body })
      .then(function (response) {
        return response.json().catch(function () {
          return {};
        }).then(function (data) {
          if (!response.ok) {
            throw new Error(data.error || "HTTP " + response.status);
          }
          return data;
        });
      });
  }

  function setStatus(text, bad) {
    statusEl.textContent = text || "";
    statusEl.classList.toggle("is-bad", !!bad);
  }

  function readField(field) {
    if (field.kind === "bool") {
      return field.input.checked;
    }
    if (field.kind === "range") {
      return parseInt(field.input.value, 10);
    }
    return field.input.value;
  }

  function changes() {
    var result = {};
    fields.forEach(function (field) {
      var value = readField(field);
      if (value !== field.initial) {
        result[field.id] = value;
      }
    });
    return result;
  }

  function updateApply() {
    applyEl.disabled = !Object.keys(changes()).length;
  }

  function buildInput(control, id) {
    var input;
    if (control.kind === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!control.value;
    } else if (control.kind === "select") {
      input = document.createElement("select");
      control.options.forEach(function (option) {
        var node = document.createElement("option");
        node.value = option;
        node.textContent = option.toUpperCase();
        input.appendChild(node);
      });
      input.value = control.value;
    } else {
      input = document.createElement("input");
      input.type = "range";
      input.min = control.min;
      input.max = control.max;
      input.step = control.step;
      input.value = control.value;
    }
    input.id = id;
    return input;
  }

  function renderRow(control, editable) {
    var row = document.createElement("div");
    row.className = "op-row";
    var box = document.createElement("div");
    box.className = "op-control";

    if (!editable) {
      var plain = document.createElement("span");
      plain.className = "op-label";
      plain.textContent = control.label;
      var shown = document.createElement("span");
      shown.className = "op-value";
      shown.textContent = String(control.value).toUpperCase();
      box.appendChild(shown);
      row.appendChild(plain);
      row.appendChild(box);
      return row;
    }

    var id = "op-" + control.id;
    var label = document.createElement("label");
    label.htmlFor = id;
    label.textContent = control.label;
    var input = buildInput(control, id);
    var field = { id: control.id, kind: control.kind, input: input };
    box.appendChild(input);
    if (control.kind === "range") {
      var value = document.createElement("span");
      value.className = "op-value";
      value.textContent = input.value;
      input.addEventListener("input", function () {
        value.textContent = input.value;
      });
      box.appendChild(value);
    }
    input.addEventListener("input", updateApply);
    input.addEventListener("change", updateApply);
    field.initial = readField(field);
    fields.push(field);
    row.appendChild(label);
    row.appendChild(box);
    return row;
  }

  function render(data) {
    formEl.textContent = "";
    fields = [];
    noteEl.hidden = !data.note;
    noteEl.textContent = data.note || "";
    data.controls.forEach(function (control) {
      formEl.appendChild(renderRow(control, data.editable));
    });
    applyEl.hidden = !data.editable;
    updateApply();
  }

  function load() {
    var seq = ++loadSeq;
    formEl.textContent = "";
    noteEl.hidden = true;
    applyEl.disabled = true;
    setStatus("Loading…");
    request("/api/cameras/" + encodeURIComponent(selectedId) + "/settings").then(function (data) {
      if (seq === loadSeq) {
        setStatus("");
        render(data);
      }
    }).catch(function (err) {
      if (seq === loadSeq) {
        setStatus(err.message, true);
      }
    });
  }

  function renderCameras() {
    camerasEl.textContent = "";
    cameras.forEach(function (camera) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "sp-chip" + (camera.id === selectedId ? " is-on" : "");
      chip.textContent = camera.display_name || camera.name;
      chip.addEventListener("click", function () {
        selectedId = camera.id;
        renderCameras();
        load();
      });
      camerasEl.appendChild(chip);
    });
  }

  applyEl.addEventListener("click", function () {
    var pending = changes();
    var seq = ++loadSeq;
    applyEl.disabled = true;
    setStatus("Sending to camera…");
    request("/api/cameras/" + encodeURIComponent(selectedId) + "/settings", {
      method: "POST",
      body: JSON.stringify({ changes: pending })
    }).then(function (data) {
      if (seq !== loadSeq) {
        return;
      }
      render(data);
      if (data.unconfirmed && data.unconfirmed.length) {
        setStatus("Sent, but Blink has not confirmed: " + data.unconfirmed.join(", "), true);
      } else {
        setStatus("Saved.");
      }
    }).catch(function (err) {
      if (seq === loadSeq) {
        setStatus(err.message, true);
        updateApply();
      }
    });
  });

  request("/api/cameras").then(function (data) {
    cameras = data.cameras || [];
    if (!cameras.length) {
      setStatus("No cameras found.", true);
      return;
    }
    selectedId = cameras[0].id;
    renderCameras();
    load();
  }).catch(function (err) {
    setStatus(err.message, true);
  });
})();

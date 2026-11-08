"""Per-camera options: what can be shown and safely written for each family.

Blink has no public API. Only fields with a documented write route are editable;
doorbells report their settings but have no documented update route, so they are
shown read-only rather than written to by guesswork.
"""

from __future__ import annotations

from typing import Any

NIGHT_VISION_CODES = {"off": 0, "on": 1, "auto": 2}
NIGHT_VISION_WORDS = {code: word for word, code in NIGHT_VISION_CODES.items()}

# Fields sent to /network/{n}/camera/{id}/update, except motion_enabled, which
# uses Blink's enable/disable action.
STANDARD_CONTROLS: tuple[dict[str, Any], ...] = (
    {"id": "motion_enabled", "label": "Motion detection", "field": "enabled", "kind": "bool"},
    {"id": "night_vision", "label": "Night vision", "field": "illuminator_enable", "kind": "night_vision"},
    {"id": "ir_intensity", "label": "IR intensity", "field": "illuminator_intensity", "kind": "range", "min": 1, "max": 10, "step": 1},
    {"id": "motion_sensitivity", "label": "Motion sensitivity", "field": "motion_sensitivity", "kind": "range", "min": 1, "max": 9, "step": 1},
    {"id": "video_length", "label": "Clip length (seconds)", "field": "video_length", "kind": "range", "min": 5, "max": 60, "step": 5},
    {"id": "retrigger", "label": "Retrigger time (seconds)", "field": "alert_interval", "kind": "range", "min": 10, "max": 60, "step": 10},
    {"id": "video_quality", "label": "Clip quality", "field": "video_quality", "kind": "select", "options_field": "video_quality_support"},
    {"id": "record_audio", "label": "Record audio", "field": "record_audio_enable", "kind": "bool"},
    {"id": "early_termination", "label": "End clips early when motion stops", "field": "early_termination", "kind": "bool"},
)

DOORBELL_FIELDS: tuple[tuple[str, str], ...] = (
    ("Motion detection", "enabled"),
    ("Night vision", "illuminator_enable_v2"),
    ("IR intensity", "illuminator_intensity"),
    ("Motion sensitivity", "motion_sensitivity"),
    ("Retrigger time (seconds)", "retrigger_time"),
    ("Clip quality", "video_quality"),
    ("Record audio", "record_audio_enable"),
    ("End clips early when motion stops", "early_termination"),
    ("Doorbell volume", "volume_control"),
    ("Status light", "led_state"),
    ("Button light", "button_led_mode"),
)

MOTION_CONTROL = "motion_enabled"
_BY_ID = {control["id"]: control for control in STANDARD_CONTROLS}


class SettingsError(ValueError):
    """A request asked for a value this camera cannot take."""


def standard_document(config: Any) -> dict[str, Any]:
    """Return the settings object from a classic camera config response."""

    cameras = config.get("camera") if isinstance(config, dict) else None
    if isinstance(cameras, list) and cameras and isinstance(cameras[0], dict):
        return cameras[0]
    return {}


def _control_value(control: dict[str, Any], raw: Any) -> Any:
    if control["kind"] == "night_vision":
        return NIGHT_VISION_WORDS.get(raw)
    if control["kind"] == "bool":
        return bool(raw)
    return raw


def describe_standard(document: dict[str, Any]) -> dict[str, Any]:
    controls = []
    for control in STANDARD_CONTROLS:
        if control["field"] not in document:
            continue
        item = {
            key: control[key]
            for key in ("id", "label", "kind", "min", "max", "step")
            if key in control
        }
        if control["kind"] == "night_vision":
            item.update(kind="select", options=list(NIGHT_VISION_CODES))
        elif control["kind"] == "select":
            options = document.get(control["options_field"])
            if not isinstance(options, list) or not options:
                continue
            item["options"] = [str(option) for option in options]
        item["value"] = _control_value(control, document[control["field"]])
        controls.append(item)
    return {"editable": True, "controls": controls}


def describe_doorbell(document: dict[str, Any]) -> dict[str, Any]:
    controls = [
        {"id": field, "label": label, "kind": "text", "value": document[field]}
        for label, field in DOORBELL_FIELDS
        if document.get(field) is not None
    ]
    return {
        "editable": False,
        "note": "Blink has no documented way to change doorbell settings. Use the Blink app.",
        "controls": controls,
    }


def _validated(control: dict[str, Any], value: Any, document: dict[str, Any]) -> Any:
    kind = control["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{control['id']} must be true or false.")
        return value
    if kind == "night_vision":
        if value not in NIGHT_VISION_CODES:
            raise SettingsError("night_vision must be off, on or auto.")
        return NIGHT_VISION_CODES[value]
    if kind == "select":
        options = document.get(control["options_field"]) or []
        if value not in options:
            raise SettingsError(f"{control['id']} is not available on this camera.")
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"{control['id']} must be a whole number.")
    low, high, step = control["min"], control["max"], control["step"]
    if value < low or value > high or (value - low) % step:
        raise SettingsError(f"{control['id']} must be {low} to {high} in steps of {step}.")
    return value


def build_update(
    changes: Any, document: dict[str, Any]
) -> tuple[dict[str, Any], bool | None]:
    """Validate changes; return the config body and any motion on/off request."""

    if not isinstance(changes, dict) or not changes:
        raise SettingsError("No changes were sent.")
    body: dict[str, Any] = {}
    motion: bool | None = None
    for key, value in changes.items():
        control = _BY_ID.get(key)
        if control is None or control["field"] not in document:
            raise SettingsError(f"{key} is not a setting on this camera.")
        checked = _validated(control, value, document)
        if key == MOTION_CONTROL:
            motion = checked
        else:
            body[control["field"]] = checked
    return body, motion


def unconfirmed(body: dict[str, Any], document: dict[str, Any]) -> list[str]:
    """Name written fields the camera's config does not report yet."""

    labels = {control["field"]: control["id"] for control in STANDARD_CONTROLS}
    return [labels[field] for field, value in body.items() if document.get(field) != value]

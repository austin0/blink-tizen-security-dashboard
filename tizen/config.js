/*
 * Defaults baked into the .wgt. The TV app stores overrides in localStorage,
 * so changing the bridge later does not require rebuilding (RED key).
 *
 * Set this to the LAN address of your bridge before building, for example:
 *   bridgeUrl: "http://192.0.2.10:8099"
 */
window.BLINK_LIVE_CONFIG = {
  bridgeUrl: "http://YOUR_BRIDGE_IP:8099",
  apiToken: "",
  audioTrack: 0,
  setupHint: "Open the pairing page on your phone."
};

# Notices and attribution

Blink / Live is licensed under the GNU General Public License, version 3
(`LICENSE`).

It is an independent project, not affiliated with Blink, Amazon, Samsung or
Tizen. “Blink” is a trademark of Immedia Semiconductor, Inc. / Amazon.

## Upstream work used or referenced

### blinkpy

- <https://github.com/fronzbot/blinkpy>
- MIT License
- Used as the Python dependency that performs Blink account authentication and
  exposes the IMMIS live-view relay. No blinkpy source is vendored in this
  repository; it is installed from a pinned upstream commit by
  `backend/requirements.txt`.

### blink2video

- <https://github.com/nico579/blink2video>
- GPL-3.0
- This bridge was built after studying blink2video's live-view design: the
  MPEG-TS → fragmented MP4 remux with `-c:v copy`, `-movflags
  frag_keyframe+empty_moov+default_base_moof`, the audio `delay_moov` detail,
  the two-AAC-track finding, and the five-minute Blink session limit. The code in
  this repository is an independent, TV-oriented implementation; it is not a
  copy of blink2video's web application.

### blink-liveview-middleware / immi2mpeg

- <https://github.com/amattu2/blink-liveview-middleware>
- <https://github.com/Wilkware/immi2mpeg>
- Protocol documentation for the IMMIS authentication frame, keep-alives and
  command polling.

### Apps2Samsung

- <https://github.com/Apps2Samsung/Apps2Samsung>
- Used to install and sign the `.wgt` on a Samsung TV in developer mode.

## Fonts

- **Bricolage Grotesque** — SIL Open Font License 1.1;
  `tizen/fonts/OFL-Bricolage.txt`.
- **IBM Plex Mono** — SIL Open Font License 1.1;
  `tizen/fonts/OFL-IBMPlexMono.txt`.

## Palette

Monokai Pro by Wimer Hazenberg. The interface uses the palette as an homage;
it is not the official Monokai Pro product.

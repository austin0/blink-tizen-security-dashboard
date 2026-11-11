/* Joins a new Blink session onto the end of an existing MSE buffer. */
(function () {
  "use strict";

  // Queued between sessions so the old session's bytes are appended first.
  var MARK = {};

  function apply(sourceBuffer) {
    // The old session can stop mid-fragment, so reset the parser first.
    sourceBuffer.abort();
    var ranges = sourceBuffer.buffered;
    var end = ranges.length ? ranges.end(ranges.length - 1) : 0;
    // Sequence mode places the next frame at the offset, whatever its timestamp.
    sourceBuffer.mode = "sequence";
    sourceBuffer.timestampOffset = end;
  }

  window.BlinkSplice = { MARK: MARK, apply: apply };
})();

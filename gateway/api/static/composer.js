/* Live SMS composer counter for the chat reply box and the test-SMS form.
 *
 * Mirrors gateway/shared/sms.py exactly: same GSM-7 tables, same 160/153 and 70/67
 * thresholds, same caps. Keep the two in sync — the server is authoritative and will
 * reject with 422 what this counter shows as over the limit.
 *
 * Progressive enhancement: without JS the forms work, they just lose the counter.
 * Attach by putting data-sms-composer="<id of the output element>" on an input.
 */
(function () {
  "use strict";

  // GSM 03.38 basic set — one septet each. Identical string to GSM7_BASIC in sms.py.
  var GSM7_BASIC =
    "@£$¥èéùìòÇ\nØø\rÅå" +
    "Δ_ΦΓΛΩΠΨΣΘΞÆæßÉ" +
    " !\"#¤%&'()*+,-./0123456789:;<=>?" +
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿" +
    "abcdefghijklmnopqrstuvwxyzäöñüà";
  // Extension set — two septets each (escape + character).
  var GSM7_EXTENSION = "\f€[]{}^~\\|";

  var MAX_SEGMENTS = 10;
  var MAX_BODY_CHARS = 1600;

  var basic = new Set(Array.from(GSM7_BASIC));
  var extended = new Set(Array.from(GSM7_EXTENSION));

  /* Characters that force UCS-2, reported by name so the operator can fix them. */
  var NAMES = {
    "‘": "'", "’": "'", "“": '"', "”": '"', "„": '"',
    "«": '"', "»": '"', "–": "-", "—": "-", "…": "...",
    "•": "-", "·": "-", "°": " deg", "→": "->", "⇒": "=>",
    "✓": "OK", "✗": "X", "™": "(TM)", "©": "(c)", "®": "(R)",
    " ": "space", "​": "(zero-width space)", "﻿": "(byte-order mark)"
  };

  function analyse(body) {
    var chars = Array.from(body); // code points, so surrogate pairs count as one char
    var offenders = [];
    var septets = 0;
    var gsm7 = true;
    for (var i = 0; i < chars.length; i++) {
      var ch = chars[i];
      if (basic.has(ch)) {
        septets += 1;
      } else if (extended.has(ch)) {
        septets += 2;
      } else {
        gsm7 = false;
        if (offenders.indexOf(ch) === -1) { offenders.push(ch); }
      }
    }
    var segments;
    if (gsm7) {
      segments = septets <= 160 ? 1 : Math.ceil(septets / 153);
    } else {
      var units = body.length; // JS string length is already UTF-16 code units
      segments = units <= 70 ? 1 : Math.ceil(units / 67);
    }
    return {
      chars: chars.length,
      encoding: gsm7 ? "GSM-7" : "UCS-2",
      perSegment: gsm7 ? (segments > 1 ? 153 : 160) : (segments > 1 ? 67 : 70),
      segments: segments,
      offenders: offenders,
      tooLong: chars.length > MAX_BODY_CHARS || segments > MAX_SEGMENTS
    };
  }

  function describe(offenders) {
    return offenders
      .slice(0, 6)
      .map(function (ch) {
        var hint = NAMES[ch];
        return hint ? ch + " (use " + hint + ")" : ch;
      })
      .join(" ");
  }

  function render(output, body) {
    if (!body) {
      output.textContent = "";
      output.classList.remove("sms-count-warn", "sms-count-bad");
      return;
    }
    var info = analyse(body);
    var text =
      info.chars + " chars · " + info.encoding + " · " +
      info.segments + "/" + MAX_SEGMENTS + " segments (" + info.perSegment + " per segment)";
    if (info.offenders.length) {
      text += " — forced to UCS-2 by: " + describe(info.offenders);
    }
    if (info.tooLong) {
      text += " — too long, this will be rejected";
    }
    output.textContent = text;
    output.classList.toggle("sms-count-bad", info.tooLong);
    output.classList.toggle("sms-count-warn", !info.tooLong && (info.segments > 1 || info.offenders.length > 0));
  }

  function attach(input) {
    var output = document.getElementById(input.getAttribute("data-sms-composer"));
    if (!output || input.dataset.smsComposerBound) { return; }
    input.dataset.smsComposerBound = "1";
    var update = function () { render(output, input.value); };
    input.addEventListener("input", update);
    // the chat form resets itself after a successful htmx post
    if (input.form) { input.form.addEventListener("reset", function () { setTimeout(update, 0); }); }
    update();
  }

  function attachAll() {
    document.querySelectorAll("[data-sms-composer]").forEach(attach);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attachAll);
  } else {
    attachAll();
  }
  // htmx swaps can replace the form; re-attach to whatever came back
  document.body.addEventListener("htmx:afterSwap", attachAll);
})();

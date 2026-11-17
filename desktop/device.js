// Optional machine identity: ~/.config/pawtrace/device.json, {"name": "laptop1", "class": "laptop"}
// (~/.config/nynx/device.json, the same shape, is also read if present). When there is one, the
// window's User-Agent gets ` PawtraceDevice/<name> PawtraceDeviceClass/<class>`, so the front end
// (and densk-core's deviceClassOf) can pick per-device sizes. No file, no change. Never written here.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const FIELD = /^[a-z][a-z0-9-]{0,31}$/;
const FILES = [["pawtrace", "device.json"], ["nynx", "device.json"]];

/** The first device file that exists and parses, as an object; null without one. */
function readDevice(home) {
  for (const parts of FILES) {
    let text;
    try {
      text = fs.readFileSync(path.join(home, ".config", ...parts), "utf8");
    } catch {
      continue; // not there: try the next one
    }
    try {
      const data = JSON.parse(text);
      return data && typeof data === "object" ? data : null;
    } catch {
      return null; // there but broken: no identity rather than another file's
    }
  }
  return null;
}

function userAgentSuffix(home) {
  const data = readDevice(home);
  if (!data) return "";
  const name = typeof data.name === "string" && FIELD.test(data.name) ? ` PawtraceDevice/${data.name}` : "";
  const cls = typeof data.class === "string" && FIELD.test(data.class) ? ` PawtraceDeviceClass/${data.class}` : "";
  return name + cls;
}

/** A window's device class from its User-Agent: pawtrace's token or the NynxDeviceClass/ one other
 *  apps' windows send; `desktop` without one. */
function deviceClassOf(userAgent) {
  return (userAgent.match(/\b(?:Pawtrace|Nynx)DeviceClass\/(desktop|laptop)(?![a-z0-9-])/) || [])[1] || "desktop";
}

module.exports = { readDevice, userAgentSuffix, deviceClassOf };

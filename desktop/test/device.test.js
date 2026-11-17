"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { deviceClassOf, readDevice, userAgentSuffix } = require("../device");

function home(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pawtrace-device-"));
  for (const [rel, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    fs.writeFileSync(path.join(dir, rel), text);
  }
  return dir;
}

test("no device file: no User-Agent suffix", () => {
  assert.strictEqual(readDevice(home({})), null);
  assert.strictEqual(userAgentSuffix(home({})), "");
});

test("pawtrace's device file wins over the nynx one", () => {
  const h = home({
    ".config/pawtrace/device.json": JSON.stringify({ name: "laptop1", class: "laptop" }),
    ".config/nynx/device.json": JSON.stringify({ name: "other", class: "desktop" }),
  });
  assert.strictEqual(userAgentSuffix(h), " PawtraceDevice/laptop1 PawtraceDeviceClass/laptop");
});

test("the nynx device file is read when pawtrace's isn't there", () => {
  const h = home({ ".config/nynx/device.json": JSON.stringify({ name: "box", class: "desktop" }) });
  assert.strictEqual(userAgentSuffix(h), " PawtraceDevice/box PawtraceDeviceClass/desktop");
});

test("a broken pawtrace device file means no identity, not the fallback's", () => {
  const h = home({ ".config/pawtrace/device.json": "{nope", ".config/nynx/device.json": JSON.stringify({ name: "box", class: "laptop" }) });
  assert.strictEqual(userAgentSuffix(h), "");
});

test("the User-Agent carries pawtrace's tokens only", () => {
  const h = home({ ".config/nynx/device.json": JSON.stringify({ name: "box", class: "laptop" }) });
  assert.doesNotMatch(userAgentSuffix(h), /Nynx/);
});

test("fields outside [a-z][a-z0-9-] are dropped", () => {
  const h = home({ ".config/pawtrace/device.json": JSON.stringify({ name: "Bad Name", class: "laptop" }) });
  assert.strictEqual(userAgentSuffix(h), " PawtraceDeviceClass/laptop");
});

test("device class from either token", () => {
  assert.strictEqual(deviceClassOf("Mozilla/5.0 Electron PawtraceDeviceClass/laptop"), "laptop");
  assert.strictEqual(deviceClassOf("Mozilla/5.0 Electron NynxDeviceClass/laptop"), "laptop");
  assert.strictEqual(deviceClassOf("Mozilla/5.0 Electron PawtraceDeviceClass/laptops"), "desktop");
  assert.strictEqual(deviceClassOf("Mozilla/5.0 Chrome"), "desktop");
});

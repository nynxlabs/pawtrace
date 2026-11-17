"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");
const { seamSettings, seamsFromFile, seamsFromKomorebi, joinedOf, payloadOf, parseEdges, expandPath, NO_SEAMS } = require("../seams");

test("seams are off without settings", () => {
  for (const raw of [undefined, null, false, "yes", [], {}]) assert.equal(seamSettings(raw, "linux"), null);
  assert.equal(seamSettings({ neighborsDir: "/x" }, "darwin"), null);
  assert.equal(seamSettings({ apps: [] }, "win32"), null);
});

test("settings expand {runtime}, {tmp} and ~", () => {
  const env = { XDG_RUNTIME_DIR: "/run/user/7" };
  const s = seamSettings({ neighborsDir: "{runtime}/seams", edgesDir: "~/edges" }, "linux", env);
  assert.deepEqual(s, { kind: "file", neighborsDir: "/run/user/7/seams", edgesDir: path.join(os.homedir(), "edges") });
  assert.equal(expandPath("{tmp}/e", {}), path.resolve(os.tmpdir(), "e"));
  const w = seamSettings({ apps: ["Other.exe"] }, "win32", env);
  assert.equal(w.kind, "komorebi");
  assert.equal(w.komorebic, "komorebic.exe");
  assert.ok(w.apps.has("other.exe"));
  assert.equal(w.edgesDir, null);
});

test("a seams-service file", () => {
  assert.deepEqual(seamsFromFile('{"left":true,"right":false,"leftPid":42,"rightPid":9}'), { left: true, right: false, leftId: 42, rightId: null });
  const withScreen = seamsFromFile('{"left":false,"right":true,"leftPid":null,"rightPid":5,"window":"0xa","windows":{"0xa":{"screenLeft":true,"screenRight":false}}}');
  assert.deepEqual(withScreen, { left: false, right: true, leftId: null, rightId: 5, screenLeft: true, screenRight: false });
  for (const bad of ["", "nope", "[]", '{"left":1,"right":false}', '{"left":true,"right":true,"leftPid":"x"}']) assert.deepEqual(seamsFromFile(bad), NO_SEAMS);
});

test("komorebi state: partners on both sides", () => {
  const w = (hwnd, exe, left) => ({ windows: { elements: [{ hwnd, exe, rect: { left, top: 0, right: 100, bottom: 500 } }], focused: 0 } });
  const state = { monitors: { elements: [{ workspaces: { elements: [{ containers: { elements: [w(1, "other.exe", 0), w(2, "pawtrace.exe", 100), w(3, "notepad.exe", 200)] } }], focused: 0 } }] } };
  const apps = new Set(["other.exe"]);
  assert.deepEqual(seamsFromKomorebi(state, 2, apps), { left: true, right: false, leftId: 1, rightId: null });
  assert.deepEqual(seamsFromKomorebi(state, 99, apps), NO_SEAMS);
});

test("joined lines and the page payload", () => {
  const seams = { left: true, right: true, leftId: 1, rightId: 2 };
  const edges = { 1: { left: false, right: true }, 2: { left: false, right: false } };
  assert.deepEqual(joinedOf(seams, { left: false, right: false }, (id) => edges[id]), { joinedLeft: true, joinedRight: false });
  assert.deepEqual(joinedOf(seams, { left: false, right: true }, () => null), { joinedLeft: false, joinedRight: true });
  assert.deepEqual(joinedOf(NO_SEAMS, { left: true, right: true }, () => null), { joinedLeft: false, joinedRight: false });
  assert.deepEqual(payloadOf({ ...seams, screenLeft: false, screenRight: true }, { left: true, right: false }, () => null), {
    left: true, right: true, screenLeft: false, screenRight: true, joinedLeft: true, joinedRight: false,
  });
  assert.deepEqual(parseEdges('{"left":true,"right":false}'), { left: true, right: false });
  assert.equal(parseEdges('{"left":true}'), null);
});

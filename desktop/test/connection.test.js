"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { parseServerUrl, readSessionFile } = require("../connection");

const TOKEN = "abcdefghijklmnopqrstuvwx";

test("only loopback URLs with a token are accepted", () => {
  assert.deepStrictEqual(parseServerUrl(`http://127.0.0.1:4711/?t=${TOKEN}`), { origin: "http://127.0.0.1:4711", token: TOKEN });
  assert.ok(parseServerUrl(`http://localhost:4711/?t=${TOKEN}`));
  for (const bad of [`http://10.0.0.2:4711/?t=${TOKEN}`, `https://127.0.0.1:4711/?t=${TOKEN}`, "http://127.0.0.1:4711/",
                     `http://127.0.0.1.evil.example:80/?t=${TOKEN}`, `http://127.0.0.1:4711/?t=short`, `http://127.0.0.1:4711/?t=${TOKEN}<x>`, 42, null]) {
    assert.strictEqual(parseServerUrl(bad), null, String(bad));
  }
});

test("session file: JSON or a bare URL; anything else is ignored", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pt-"));
  const f = path.join(dir, "remote.json");
  assert.strictEqual(readSessionFile(f), null);
  fs.writeFileSync(f, JSON.stringify({ url: `http://127.0.0.1:5000/?t=${TOKEN}` }));
  assert.strictEqual(readSessionFile(f).origin, "http://127.0.0.1:5000");
  fs.writeFileSync(f, `http://127.0.0.1:5001/?t=${TOKEN}\n`);
  assert.strictEqual(readSessionFile(f).origin, "http://127.0.0.1:5001");
  fs.writeFileSync(f, "{not json");
  assert.strictEqual(readSessionFile(f), null);
  fs.writeFileSync(f, JSON.stringify({ url: `http://192.168.1.2:5000/?t=${TOKEN}` }));
  assert.strictEqual(readSessionFile(f), null);
});

#!/usr/bin/env node
/**
 * Drive fixture-demo through real button clicks via CDP (headless Chrome/Edge).
 * Usage: node scripts/capture_fixture_cdp.mjs <url> <out.png> <width> <height> <testid> [<testid> ...]
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

function findBrowser() {
  if (process.env.CAPTURE_BROWSER) return process.env.CAPTURE_BROWSER;
  const candidates = [
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return "msedge";
}

const browser = findBrowser();

const args = process.argv.slice(2);
if (args.length < 5) {
  console.error("usage: capture_fixture_cdp.mjs <url> <out.png> <width> <height> <testid> [...]");
  process.exit(1);
}
const [url, outPath, width, height, ...clicks] = args;
const port = 9500 + Math.floor(Math.random() * 400);
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "opsdesk-fixture-"));

function wait(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function httpGet(u) {
  return new Promise((resolve, reject) => {
    http.get(u, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => resolve(JSON.parse(data)));
    }).on("error", reject);
  });
}

async function cdpSession(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res);
    ws.addEventListener("error", rej);
  });
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, (msg) => (msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)));
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
  await send("Page.enable");
  await send("Runtime.enable");
  return { send, close: () => ws.close() };
}

async function main() {
  const proc = spawn(
    browser,
    [
      "--headless=new",
      "--disable-gpu",
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${userData}`,
      `--window-size=${width},${height}`,
      "about:blank",
    ],
    { stdio: "ignore" },
  );
  try {
    await wait(800);
    const tabs = await httpGet(`http://127.0.0.1:${port}/json/list`);
    const tab = tabs.find((t) => t.type === "page") || tabs[0];
    if (!tab?.webSocketDebuggerUrl) throw new Error("no CDP page target");
    const session = await cdpSession(tab.webSocketDebuggerUrl);
    await session.send("Page.navigate", { url });
    await wait(700);
    await session.send("Runtime.evaluate", { expression: "localStorage.clear();" });
    await session.send("Page.navigate", { url });
    await wait(800);
    for (const testid of clicks) {
      const expr = `(function(){const b=document.querySelector('[data-testid="${testid}"]'); if(!b) throw new Error('missing ${testid}'); b.click();})()`;
      await session.send("Runtime.evaluate", { expression: expr });
      await wait(350);
    }
    await wait(300);
    const shot = await session.send("Page.captureScreenshot", { format: "png", fromSurface: true });
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, Buffer.from(shot.data, "base64"));
    session.close();
    console.log("wrote", outPath, `${width}x${height}`, clicks.join(">"));
  } finally {
    proc.kill();
    try {
      fs.rmSync(userData, { recursive: true, force: true });
    } catch (_) {
      /* edge may still hold profile lock briefly on Windows */
    }
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

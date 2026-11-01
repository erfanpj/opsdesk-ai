#!/usr/bin/env node
/**
 * CDP capture: real clicks, Emulation.setDeviceMetricsOverride (DPR 1), scroll targets, PNG meta line.
 * Usage: node capture_fixture_cdp.mjs <url> <out.png> <cssWidth> <cssHeight> [data-testid ...]
 * Env: CAPTURE_SCROLL=comma,separated,selectors  CAPTURE_SCROLL_JS=expression returning undefined
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

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
if (args.length < 4) {
  console.error("usage: capture_fixture_cdp.mjs <url> <out.png> <cssWidth> <cssHeight> [testid ...]");
  process.exit(1);
}
const [url, outPath, width, height, ...clicks] = args;
const cssW = parseInt(width, 10);
const cssH = parseInt(height, 10);
const port = 9500 + Math.floor(Math.random() * 400);
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "opsdesk-fixture-"));
const scrollSels = (process.env.CAPTURE_SCROLL || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const scrollJs = process.env.CAPTURE_SCROLL_JS || "";

function wait(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function httpGet(u) {
  return new Promise((resolve, reject) => {
    http
      .get(u, (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => resolve(JSON.parse(data)));
      })
      .on("error", reject);
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
  await send("Emulation.setDeviceMetricsOverride", {
    width: cssW,
    height: cssH,
    deviceScaleFactor: 1,
    mobile: cssW < 600,
  });
  return { send, close: () => ws.close() };
}

function pngSize(filePath) {
  const buf = fs.readFileSync(filePath);
  return { pngWidth: buf.readUInt32BE(16), pngHeight: buf.readUInt32BE(20) };
}

async function scrollIntoView(session) {
  for (const sel of scrollSels) {
    await session.send("Runtime.evaluate", {
      expression: `(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (el) el.scrollIntoView({block: "center", inline: "nearest", behavior: "instant"}); })()`,
    });
    await wait(180);
  }
  if (scrollJs) {
    await session.send("Runtime.evaluate", { expression: scrollJs });
    await wait(220);
  }
}

async function main() {
  const proc = spawn(
    browser,
    [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${userData}`,
      `--window-size=${cssW},${cssH}`,
      "about:blank",
    ],
    { stdio: "ignore" },
  );
  try {
    await wait(900);
    const tabs = await httpGet(`http://127.0.0.1:${port}/json/list`);
    const tab = tabs.find((t) => t.type === "page") || tabs[0];
    if (!tab?.webSocketDebuggerUrl) throw new Error("no CDP page target");
    const session = await cdpSession(tab.webSocketDebuggerUrl);
    await session.send("Page.navigate", { url });
    await wait(700);
    if (clicks.length) {
      await session.send("Runtime.evaluate", { expression: "localStorage.clear();" });
      await session.send("Page.navigate", { url });
      await wait(800);
      for (const testid of clicks) {
        const expr = `(function(){const b=document.querySelector('[data-testid="${testid}"]'); if(!b) throw new Error('missing ${testid}'); b.scrollIntoView({block:'center',behavior:'instant'}); b.click();})()`;
        await session.send("Runtime.evaluate", { expression: expr });
        await wait(400);
      }
    }
    await wait(250);
    await scrollIntoView(session);
    const dims = await session.send("Runtime.evaluate", {
      expression: "({ innerWidth: window.innerWidth, innerHeight: window.innerHeight, devicePixelRatio: window.devicePixelRatio })",
      returnByValue: true,
    });
    const shot = await session.send("Page.captureScreenshot", { format: "png", fromSurface: true, captureBeyondViewport: false });
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, Buffer.from(shot.data, "base64"));
    const { pngWidth, pngHeight } = pngSize(outPath);
    session.close();
    const meta = {
      cssViewport: `${cssW}x${cssH}`,
      innerWidth: dims.result.value.innerWidth,
      innerHeight: dims.result.value.innerHeight,
      devicePixelRatio: dims.result.value.devicePixelRatio,
      pngWidth,
      pngHeight,
      clicks: clicks.join(">") || "(navigate only)",
    };
    console.log("wrote", outPath, JSON.stringify(meta));
    console.log("CAPTURE_META" + JSON.stringify(meta));
  } finally {
    proc.kill();
    try {
      fs.rmSync(userData, { recursive: true, force: true });
    } catch (_) {
      /* Windows profile lock */
    }
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

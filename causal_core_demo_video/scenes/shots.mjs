// Screenshots of real pages (the running Astronomy Shop, or a local scene) with headless Chrome. No packages needed.
//
//   node scenes/shots.mjs OUT_DIR name=URL [name=URL ...] [--w 1920 --h 1080 --wait 2500]
//   e.g. node scenes/shots.mjs videos/uc2_images shop_home=http://localhost:8080/
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); if (i < 0) return d; const v = args[i + 1]; args.splice(i, 2); return v; };
const W = +opt("--w", 1920), H = +opt("--h", 1080), WAIT = +opt("--wait", 2500);
const out = resolve(args.shift());
const pages = args.map(a => { const i = a.indexOf("="); return [a.slice(0, i), a.slice(i + 1)]; });
const sleep = ms => new Promise(r => setTimeout(r, ms));
mkdirSync(out, { recursive: true });

const port = 9800 + Math.floor(Math.random() * 150), profile = mkdtempSync(join(tmpdir(), "shots-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--window-size=${W},${H}`, "--hide-scrollbars",
  "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
let ws, id = 0; const pending = new Map(), waiters = [];
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
try {
  let target;
  for (let k = 0; k < 100 && !target; k++) { try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page"); } catch { await sleep(100); } }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  ws.onmessage = m => { const msg = JSON.parse(m.data); if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); } else if (msg.method) for (let i = waiters.length - 1; i >= 0; i--) if (waiters[i].name === msg.method) { waiters[i].res(msg.params); waiters.splice(i, 1); } };
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  for (const [name, url] of pages) {
    const loaded = new Promise(r => waiters.push({ name: "Page.loadEventFired", res: r }));
    await send("Page.navigate", { url });
    await Promise.race([loaded, sleep(15000)]); await sleep(WAIT);
    const { data } = await send("Page.captureScreenshot", { format: "png", fromSurface: true });
    writeFileSync(join(out, `${name}.png`), Buffer.from(data, "base64"));
    console.log(`  ${name}.png  ← ${url}`);
  }
} finally { try { ws?.close(); } catch {} chrome.kill("SIGKILL"); try { rmSync(profile, { recursive: true, force: true }); } catch {} }

// Record a scene page to MP4: headless Chrome renders window.render(t) frame by frame; ffmpeg encodes.
// No packages needed (Node 22+ has fetch and WebSocket).
//
//   node scenes/record.mjs uc1                    → videos/uc1.mp4 (30 fps, 1920×1080)
//   node scenes/record.mjs uc1 --stills 5,26,60   → videos/stills/uc1_t5.png … (for review)
//   node scenes/record.mjs uc1 --audio videos/uc1_voice.m4a   → mux a voice-over track
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const args = process.argv.slice(2);
const uc = args[0] || "uc1";
const opt = k => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : undefined; };
const FPS = +(opt("--fps") || 30), W = 1920, H = 1080;
const stills = opt("--stills")?.split(",").map(Number);
const audio = opt("--audio");
const sleep = ms => new Promise(r => setTimeout(r, ms));

const port = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), "rec-chrome-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--window-size=${W},${H}`, "--hide-scrollbars",
  "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check", "--allow-file-access-from-files",
  "--font-render-hinting=none", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });

let ws, id = 0;
const pending = new Map(), waiters = [];
function send(method, params = {}) {
  return new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
}
const once = name => new Promise(res => waiters.push({ name, res }));

try {
  let target;
  for (let k = 0; k < 100 && !target; k++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page"); } catch { await sleep(100); }
  }
  if (!target) throw new Error("Chrome did not start");
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = m => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); }
    else if (msg.method) for (let i = waiters.length - 1; i >= 0; i--) if (waiters[i].name === msg.method) { waiters[i].res(msg.params); waiters.splice(i, 1); }
  };
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  const loaded = once("Page.loadEventFired");
  await send("Page.navigate", { url: pathToFileURL(join(HERE, `${uc}.html`)).href + "?record=1" });
  await loaded; await sleep(300);
  const ev = async expr => { const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };
  const total = await ev("window.TOTAL");
  if (!total) throw new Error("page did not define window.TOTAL (data file missing?)");
  const shot = async (t, format) => { await ev(`window.render(${t}); 0`); return Buffer.from((await send("Page.captureScreenshot", { format, quality: format === "jpeg" ? 92 : undefined, fromSurface: true })).data, "base64"); };

  if (stills) {
    const dir = join(ROOT, "videos", "stills"); mkdirSync(dir, { recursive: true });
    for (const t of stills) { writeFileSync(join(dir, `${uc}_t${t}.png`), await shot(t, "png")); }
    console.log(`wrote ${stills.length} stills to videos/stills/ (total ${total}s)`);
  } else {
    mkdirSync(join(ROOT, "videos"), { recursive: true });
    const out = join(ROOT, "videos", `${uc}.mp4`);
    const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
      ...(audio ? ["-i", audio, "-c:a", "aac", "-b:a", "160k", "-shortest"] : []),
      "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], { stdio: ["pipe", "inherit", "inherit"] });
    const n = Math.round(total * FPS), t0 = Date.now();
    for (let f = 0; f < n; f++) {
      const buf = await shot(f / FPS, "jpeg");
      if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once("drain", r));
      if (f % (FPS * 10) === 0) process.stdout.write(`\r  frame ${f}/${n} (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
    }
    ff.stdin.end();
    const code = await new Promise(r => ff.on("close", r));
    if (code !== 0) throw new Error(`ffmpeg exited ${code}`);
    console.log(`\nwrote videos/${uc}.mp4: ${n} frames, ${total}s, in ${((Date.now() - t0) / 1000).toFixed(0)} s`);
  }
} finally {
  try { ws?.close(); } catch {}
  chrome.kill("SIGKILL");
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}

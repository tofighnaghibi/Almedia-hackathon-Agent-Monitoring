// Real-time capture of a live page (the video-2 dashboard) to MP4 while the scenario runs. Headless Chrome, 1920×1080,
// a fixed frame rate (frames are repeated when a capture runs late, so wall-clock timing is kept).
//
//   node scenes/capture_live.mjs URL OUT.mp4 [--fps 15] [--stop-file PATH] [--max-s 900]
// Stops when STOP-FILE appears (the record script creates it after the run), or after --max-s seconds.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const [url, out] = args;
const FPS = +opt("--fps", 15), STOP = opt("--stop-file"), MAX_S = +opt("--max-s", 900), W = 1920, H = 1080;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const port = 9600 + Math.floor(Math.random() * 150), profile = mkdtempSync(join(tmpdir(), "cap-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--window-size=${W},${H}`, "--hide-scrollbars",
  "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
let ws, id = 0; const pending = new Map();
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
const stamps = [];
try {
  let target;
  for (let k = 0; k < 100 && !target; k++) { try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page"); } catch { await sleep(100); } }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  ws.onmessage = m => { const msg = JSON.parse(m.data); if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); } };
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url }); await sleep(1500);
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], { stdio: ["pipe", "inherit", "inherit"] });
  const t0 = Date.now(); let frames = 0, last = null;
  console.log(`capturing ${url} → ${out} at ${FPS} fps (started ${new Date(t0).toISOString()})`);
  while ((Date.now() - t0) / 1000 < MAX_S && !(STOP && existsSync(STOP))) {
    const { data } = await send("Page.captureScreenshot", { format: "jpeg", quality: 88, fromSurface: true });
    last = Buffer.from(data, "base64");
    const due = Math.floor((Date.now() - t0) / 1000 * FPS) + 1;      // keep wall-clock timing: repeat frames if late
    while (frames < due) { if (!ff.stdin.write(last)) await new Promise(r => ff.stdin.once("drain", r)); frames++; }
    stamps.push(Date.now());
    const wait = t0 + frames / FPS * 1000 - Date.now();
    if (wait > 0) await sleep(wait);
  }
  ff.stdin.end(); await new Promise(r => ff.on("close", r));
  writeFileSync(out.replace(/\.mp4$/, ".start.txt"), new Date(t0).toISOString() + "\n");
  console.log(`wrote ${out}: ${frames} frames, ${((Date.now() - t0) / 1000).toFixed(0)} s, ${stamps.length} unique captures`);
} finally { try { ws?.close(); } catch {} chrome.kill("SIGKILL"); try { rmSync(profile, { recursive: true, force: true }); } catch {} }

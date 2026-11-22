"""Cut the video-2 screen recording to a 70 s demo segment and place the narration on the final timeline.

Reads outputs/uc2/timeline.json (wall times of the run's key moments), videos/uc2_screen.mp4 (+ .start.txt), and
scenes/uc2_narration.json; writes videos/uc2_demo_cut.mp4, scenes/uc2_narration_resolved.json, videos/uc2_narration.srt
and NARRATION_UC2.md. The incident itself (alarm → fix) plays at real speed when it fits; the rest is sped up.

    python3 scenes/cut_uc2.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
V = HERE / "videos"
SEG = {"open": 0.0, "solution": 32.0, "demo": 96.0, "close": 166.0}
DEMO_LEN = 70.0
BUDGET = {"normal": 14.0, "before_alarm": 8.0, "incident_max": 40.0}


def T(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main() -> int:
    tl = json.loads((HERE / "outputs/uc2/timeline.json").read_text())
    start = T((V / "uc2_screen.start.txt").read_text().strip())
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(V / "uc2_screen.mp4")],
                               capture_output=True, text=True).stdout)
    rel = lambda e: T(e["wall"]) - start
    days = [e for e in tl if e["kind"] == "day"]
    paced = [e for e in days if e.get("paced")]
    first = lambda k: next(e for e in tl if e["kind"] == k)
    alarm, execu = first("engine"), first("execution")
    fault = next(e for e in days if e["text"] == json.loads((HERE / "outputs/uc2/world.json").read_text())["fault_day"])
    after = next((e for e in days if rel(e) > rel(fault)), None)
    a0 = rel(paced[0]) if paced and rel(paced[0]) < rel(fault) else max(0.0, rel(fault) - 40)
    parts = [("normal", a0, rel(fault), BUDGET["normal"]),
             ("before_alarm", rel(fault), rel(alarm) - 3, BUDGET["before_alarm"]),
             ("incident", rel(alarm) - 3, rel(execu) + 4, None),
             ("after", rel(execu) + 4, min(dur, (rel(after) + 2) if after else rel(execu) + 30), None)]
    inc_len = parts[2][2] - parts[2][1]
    inc_out = min(inc_len, BUDGET["incident_max"])
    after_out = DEMO_LEN - BUDGET["normal"] - BUDGET["before_alarm"] - inc_out
    parts[2] = (*parts[2][:3], inc_out)
    parts[3] = (*parts[3][:3], after_out)
    filt, labels, offset, anchors = [], [], 0.0, {"demo_start": 0.0, "fault_day": BUDGET["normal"]}
    for i, (name, a, b, out_len) in enumerate(parts):
        k = (b - a) / out_len                                    # speed factor (>1 = faster)
        filt.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{k:.6f}[v{i}]")
        labels.append(f"[v{i}]")
        for e in tl:
            if e["kind"] in ("engine", "policy", "proposal", "approval", "execution") and a <= rel(e) < b:
                key = "alarm" if e["kind"] == "engine" else e["kind"]
                anchors.setdefault(key, offset + (rel(e) - a) / k)
        offset += out_len
    filt.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0,fps=30[out]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(V / "uc2_screen.mp4"), "-filter_complex", ";".join(filt), "-map", "[out]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(V / "uc2_demo_cut.mp4")], check=True)
    # narration on the final timeline
    close = json.loads((HERE / "scenes/uc2_data.js").read_text()[len("window.DATA = "):-2])["close"]
    fill = {"exp": f"{close['exp']:.0f}", "lo": f"{close['lo']:.0f}", "hi": f"{close['hi']:.0f}",
            "attempts": str(close["attempts"]), "usual": str(close["usual"])}
    base = {"open": SEG["open"], "solution": SEG["solution"], "close": SEG["close"]} | {k: SEG["demo"] + v for k, v in anchors.items()}
    lines = []
    for ln in json.loads((HERE / "scenes/uc2_narration.json").read_text())["lines"]:
        if ln["anchor"] not in base:
            print(f"  (skipped, anchor not in this run: {ln['anchor']})")
            continue
        lines.append({"t": round(base[ln["anchor"]] + ln["at"], 2), "text": ln["text"].format(**fill)})
    lines.sort(key=lambda x: x["t"])
    for i, ln in enumerate(lines):                              # a line shows until the next one starts (max 9 s)
        ln["end"] = round(min(lines[i + 1]["t"] if i + 1 < len(lines) else 180.0, ln["t"] + 9.0), 2)
    (HERE / "scenes/uc2_narration_resolved.json").write_text(json.dumps({"lines": lines, "anchors": base}, indent=1))
    ts = lambda s: f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d},{int(round(s % 1 * 1000)) % 1000:03d}"
    (V / "uc2_narration.srt").write_text("\n".join(f"{i + 1}\n{ts(l['t'])} --> {ts(l['end'])}\n{l['text']}\n" for i, l in enumerate(lines)))
    mm = lambda s: f"{int(s // 60)}:{int(s % 60):02d}"
    seg_name = lambda s: "1 · Pitch" if s < 32 else "2 · How it works" if s < 96 else "3 · Demo (live run)" if s < 166 else "4 · Close"
    md, cur = ["# Video 2 narration (3:00)", "", "Read at a calm pace; each line starts at the time shown. The prompter "
               "(`scripts/uc2_voice.sh`) shows the same lines in sync with the video.", ""], None
    for l in lines:
        if seg_name(l["t"]) != cur:
            cur = seg_name(l["t"]); md += ["", f"## {cur}", ""]
        md.append(f"- **{mm(l['t'])}** {l['text']}")
    (HERE / "NARRATION_UC2.md").write_text("\n".join(md) + "\n")
    print(f"wrote videos/uc2_demo_cut.mp4 ({DEMO_LEN:.0f} s; incident at {inc_len / inc_out:.2f}x), NARRATION_UC2.md, "
          f"videos/uc2_narration.srt ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

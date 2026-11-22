"""Local-only server for the voice-over prompter: serves this folder and saves POST /save-voice to videos/uc2_voice.webm.

    python3 scenes/voice_server.py [port]        # binds 127.0.0.1 only
"""
import http.server
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/save-voice":
            self.send_error(404); return
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > 200_000_000:
            self.send_error(400); return
        (ROOT / "videos").mkdir(exist_ok=True)
        (ROOT / "videos/uc2_voice.webm").write_bytes(self.rfile.read(n))
        off = parse_qs(u.query).get("offset", ["0"])[0]
        (ROOT / "videos/uc2_voice.offset").write_text(str(float(off)))
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        print(f"saved videos/uc2_voice.webm ({n:,} bytes), offset {off}s", flush=True)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    print(f"prompter: http://127.0.0.1:{port}/scenes/uc2_prompter.html  (Ctrl-C to stop)", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()

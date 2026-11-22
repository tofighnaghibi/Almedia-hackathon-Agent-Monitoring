"""The technical rehearsal of the demo lifecycle must keep passing on main: clean data raises nothing, an injected fault opens an
incident, a rollback plus normal hours resolves it, a planned promo is only 'explained', a late source is only 'pending'."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_demo_lifecycle_rehearsal_passes(tmp_path):
    run = tmp_path / "run"
    # a bare environment, plus what Windows needs to start Python at all (SYSTEMROOT) and a place for temporary files
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    env.update({k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PROGRAMDATA") if k in os.environ})
    out = subprocess.run([sys.executable, "scripts/rehearse_core.py", "--run", str(run)], cwd=ROOT, capture_output=True, text=True,
                         timeout=240, env=env)
    assert out.returncode == 0, out.stdout[-400:] + out.stderr[-800:]
    result = json.loads((run / "rehearsal.json").read_text(encoding="utf-8"))
    assert result["passed"] is True
    stages = {s["stage"]: s for s in result["stages"]}
    assert stages["clean"]["incidents"] == []
    assert stages["payment injected"]["incidents"] and stages["payment injected"]["incidents"][0]["status"] == "open"
    assert stages["promo hours"]["non_alert_statuses"] == ["explained"]
    assert "pending" in stages["payments delayed"]["non_alert_statuses"]
    last_alert_stage = [s for s in result["stages"] if s["stage"] == "promo hours"][-1]
    assert [i["status"] for i in last_alert_stage["incidents"]] == ["resolved"]

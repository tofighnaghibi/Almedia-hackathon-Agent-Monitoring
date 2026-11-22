import os
import sys
from pathlib import Path

# The test suite always runs without an LLM key: templates only, fully deterministic.
os.environ["ARGUS_NO_DOTENV"] = "1"
for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    os.environ.pop(k, None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

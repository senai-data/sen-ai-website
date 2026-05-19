"""pytest harness for worker/ — adds the worker dir to sys.path so tests can
`from services.intent_taxonomy import …` exactly like the runtime handlers do
(no `worker.` prefix). Run from the repo root:

    pytest worker/tests/

or from the worker dir itself:

    cd worker && pytest tests/
"""

from __future__ import annotations

import sys
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parent.parent
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

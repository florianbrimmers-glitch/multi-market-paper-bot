"""Put the repo root on sys.path so `import config` etc. work from any pytest invocation."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

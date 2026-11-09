import os
import sys
import tempfile
from pathlib import Path

# Tests never touch real APIs: force demo mode and a throwaway cache before app imports.
os.environ["DEMO_MODE"] = "1"
os.environ["CACHE_PATH"] = str(Path(tempfile.mkdtemp()) / "test-cache.sqlite")
for k in ("ANTHROPIC_API_KEY", "KEEPA_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "APP_PASSWORD"):
    os.environ.pop(k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

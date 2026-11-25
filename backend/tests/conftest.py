import os
import sys
import tempfile
from pathlib import Path

# Va impostato PRIMA di importare l'app: la configurazione si legge all'import.
_tmp = tempfile.mkdtemp(prefix="splat-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["SPLAT_FAKE"] = "1"
os.environ["SPLAT_FAKE_SPEED"] = "200"
os.environ["AUTO_RESUME"] = "0"
os.environ["STATIC_DIR"] = str(Path(_tmp) / "static")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

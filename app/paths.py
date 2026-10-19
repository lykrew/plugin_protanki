from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SERVERS_PATH = DATA_DIR / "servers.json"
SETTINGS_PATH = DATA_DIR / "settings.json"

MODES = ("DM", "TDM", "CTF", "CP")

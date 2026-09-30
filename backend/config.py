import ipaddress
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "homelab.db"
SEED_PATH = ROOT / "seed.json"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5232"))


def _required_env(name):
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} must be set")
    return value


AUTH_USERNAME = _required_env("AUTH_USERNAME")
AUTH_PASSWORD = _required_env("AUTH_PASSWORD")
SESSION_TTL = int(os.getenv("SESSION_TTL", "43200"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _proxy_networks(raw):
    """P29: um valor inválido impede o arranque, em vez de ser ignorado em silêncio."""
    networks = []
    for value in (x.strip() for x in raw.split(",")):
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            raise RuntimeError(
                f"TRUSTED_PROXY_IPS inválido: {value!r}. Usa IPs ou redes separados por vírgulas "
                "(ex.: 10.0.0.5,10.0.0.0/24)."
            )
    return tuple(networks)


TRUSTED_PROXY_IPS = _proxy_networks(os.getenv("TRUSTED_PROXY_IPS", ""))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
# P17/P05: número de backups automáticos guardados em data/backups/ (por tipo)
AUTO_BACKUPS_KEEP = max(1, int(os.getenv("AUTO_BACKUPS_KEEP", "5")))

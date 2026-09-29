"""Environment configuration; no hard-coded keys or unofficial endpoints."""

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from string import Formatter
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw not in {"true", "false"}:
        raise ConfigError(f"{name} muss true oder false sein.")
    return raw == "true"


def _int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine ganze Zahl sein.") from exc
    if not lower <= value <= upper:
        raise ConfigError(f"{name} muss zwischen {lower} und {upper} liegen.")
    return value


def validate_api_url(template: str) -> None:
    try:
        fields = []
        for _, name, spec, conversion in Formatter().parse(template):
            if name is not None:
                if name not in {"platform", "username"} or spec or conversion:
                    raise ValueError
                fields.append(name)
        if set(fields) != {"platform", "username"}:
            raise ValueError
        parsed = urlsplit(template)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment
            or "{" in parsed.netloc or "}" in parsed.netloc
            or any(c.isspace() or ord(c) < 32 for c in template)
        ):
            raise ValueError
        # A standard Tracker developer key cannot unlock an R6 endpoint.
        if parsed.hostname.endswith(("tracker.gg", "tracker.network")):
            raise ConfigError(
                "Tracker bietet keine öffentliche R6-API. Trage hier erst einen "
                "nutzbaren R6-Dienst mit dem Format aus docs/API_SCHEMA.md ein."
            )
    except ValueError as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(
            "STATS_API_URL braucht eine HTTPS-Adresse mit {platform} und {username}; "
            "keine Zugangsdaten in der URL."
        ) from exc


@dataclass(frozen=True, slots=True)
class Config:
    discord_token: str = field(repr=False, default="")
    data_mode: str = "arenyze"
    arenyze_key: str = field(repr=False, default="")
    api_url: str = ""
    api_key: str = field(repr=False, default="")
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    private: bool = False
    cache_ttl: int = 60
    api_timeout: int = 15
    requests_per_minute: int = 30

    @classmethod
    def load(cls, *, require_token: bool = True) -> "Config":
        load_dotenv(ROOT / ".env", override=False)
        mode = os.getenv("DATA_MODE", "arenyze").strip().lower()
        if mode not in {"arenyze", "demo", "http", "disabled"}:
            raise ConfigError("DATA_MODE muss arenyze, demo, http oder disabled sein.")
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if require_token and (not token or token == "DEIN_DISCORD_BOT_TOKEN"):
            raise ConfigError("Trage zuerst DISCORD_TOKEN in der .env-Datei ein.")
        arenyze_key = os.getenv("ARENYZE_API_KEY", "").strip()
        if require_token and mode == "arenyze" and (not arenyze_key or arenyze_key == "DEIN_ARENYZE_API_KEY"):
            raise ConfigError("Trage ARENYZE_API_KEY in .env ein oder wähle DATA_MODE=demo für Beispieldaten.")
        if any(ord(c) < 32 for c in arenyze_key):
            raise ConfigError("ARENYZE_API_KEY darf keine Steuerzeichen enthalten.")
        api_url = os.getenv("STATS_API_URL", "").strip()
        if mode == "http":
            validate_api_url(api_url)
        header = os.getenv("STATS_API_KEY_HEADER", "Authorization").strip()
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header):
            raise ConfigError("STATS_API_KEY_HEADER ist kein gültiger Headername.")
        if header.lower() in {"host", "content-length", "transfer-encoding", "connection"}:
            raise ConfigError("STATS_API_KEY_HEADER darf kein Transport-Header sein.")
        key = os.getenv("STATS_API_KEY", "").strip()
        prefix = os.getenv("STATS_API_KEY_PREFIX", "Bearer ")
        if "\r" in key + prefix or "\n" in key + prefix:
            raise ConfigError("API-Key und Präfix dürfen keine Zeilenumbrüche enthalten.")
        return cls(
            discord_token=token, data_mode=mode, arenyze_key=arenyze_key, api_url=api_url,
            api_key=key, api_key_header=header, api_key_prefix=prefix,
            private=_bool("STATS_PRIVATE", False),
            cache_ttl=_int("CACHE_TTL_SECONDS", 60, 0, 3600),
            api_timeout=_int("API_TIMEOUT_SECONDS", 15, 1, 45),
            requests_per_minute=_int("API_REQUESTS_PER_MINUTE", 30, 1, 600),
        )

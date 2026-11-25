"""Geografia simples: distância, rumo, caixa da região e geocodificação (Nominatim) com cache."""
from __future__ import annotations

import json
import math
import time
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Callable

from . import http

NOMINATIM = "https://nominatim.openstreetmap.org/search"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def directions_compatible(a: float | None, b: float | None, tolerance: float = 45.0) -> bool:
    """Sem sentido informado em um dos lados = compatível; sentidos opostos NÃO são o mesmo radar."""
    if a is None or b is None:
        return True
    return angle_diff(a, b) <= tolerance


def in_box(lat: float, lon: float, box: tuple[float, float, float, float]) -> bool:
    south, west, north, east = box
    return south <= lat <= north and west <= lon <= east


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm_name(s: str) -> str:
    return " ".join(strip_accents(s).casefold().split())


CARDINALS = {"n": 0, "ne": 45, "e": 90, "se": 135, "s": 180, "sw": 225, "so": 225, "w": 270, "o": 270, "nw": 315, "no": 315}


def parse_direction(value: str | None) -> float | None:
    """OSM 'direction' pode ser graus ou ponto cardeal. Qualquer outra coisa = desconhecido (nunca chuta)."""
    if not value:
        return None
    v = value.strip().lower()
    try:
        deg = float(v.replace(",", "."))
        return deg % 360 if 0 <= deg <= 360 else None
    except ValueError:
        return float(CARDINALS[v]) if v in CARDINALS else None


class Geocoder:
    """Nominatim público: 1 pedido por segundo, User-Agent identificado, resultados em cache (política de uso do OSM)."""

    def __init__(self, cache_path: Path | None, fetch: Callable[[str], str] | None = None, sleep: Callable[[float], None] = time.sleep):
        self.cache_path = cache_path
        self.fetch = fetch or (lambda url: http.get_text(url, timeout=30))
        self.sleep = sleep
        self.cache: dict[str, list] = {}
        self._last = 0.0
        if cache_path and cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except ValueError:
                self.cache = {}

    def save(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    def lookup(self, street: str, city: str, box: tuple[float, float, float, float]) -> tuple[float, float] | None:
        """Devolve (lat, lon) só se o resultado cair na região E na cidade pedida; senão None."""
        q = f"{street}, {city}, SP, Brasil"
        if q not in self.cache:
            wait = 1.1 - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            url = NOMINATIM + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 3, "addressdetails": 1, "countrycodes": "br"})
            try:
                self.cache[q] = json.loads(self.fetch(url))
            except (http.HttpError, ValueError):
                return None  # não grava no cache: tenta de novo na próxima execução
            self._last = time.monotonic()
        for item in self.cache[q]:
            try:
                lat, lon = float(item["lat"]), float(item["lon"])
            except (KeyError, ValueError, TypeError):
                continue
            addr = item.get("address", {})
            places = {norm_name(str(addr.get(k, ""))) for k in ("city", "town", "municipality", "village", "city_district")}
            if in_box(lat, lon, box) and norm_name(city) in places:
                return lat, lon
        return None

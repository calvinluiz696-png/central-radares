"""Coleta determinística (sem IA): páginas públicas -> texto, OpenStreetMap (Overpass) e a base do Pardal."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from . import http
from .geo import parse_direction

PARDAL_URL = "https://raw.githubusercontent.com/Murkiriel/pardal/main/estados/SP/radares.csv"
OVERPASS = "https://overpass-api.de/api/interpreter"


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("p", "br", "li", "tr", "h1", "h2", "h3", "h4", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = "".join(p.parts)
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class BasePoint:
    lat: float
    lng: float
    kind: str
    bearing: float | None
    limit_kmh: int | None = None
    source: str = ""


def parse_pardal(text: str) -> list[BasePoint]:
    """Lê o CSV do Pardal (cabeçalho documentado). Linhas ruins são ignoradas; só pontos ativos entram na comparação."""
    out: list[BasePoint] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for row in reader:
        try:
            if str(row.get("active", "1")).strip() == "0":
                continue
            lat, lng = float(row["lat"]), float(row["lng"])
        except (KeyError, ValueError, TypeError):
            continue
        d = row.get("direction_deg") or ""
        try:
            bearing = float(d) if d.strip() else None
        except ValueError:
            bearing = None
        lim = row.get("limit_kmh") or ""
        out.append(BasePoint(lat, lng, (row.get("kind") or "FIXED").strip().upper(), bearing,
                             int(lim) if lim.strip().isdigit() else None, row.get("source", "")))
    return out


def overpass_query(box: tuple[float, float, float, float]) -> str:
    s, w, n, e = box
    return f'[out:json][timeout:90];node["highway"="speed_camera"]({s},{w},{n},{e});out body;'


@dataclass
class OsmCamera:
    osm_id: int
    lat: float
    lng: float
    limit_kmh: int | None
    bearing: float | None


def fetch_osm_cameras(box: tuple[float, float, float, float], post=http.post_form) -> list[OsmCamera]:
    import json
    import urllib.parse
    raw = post(OVERPASS, "data=" + urllib.parse.quote(overpass_query(box)))
    out = []
    for el in json.loads(raw).get("elements", []):
        if el.get("type") != "node":
            continue
        tags = el.get("tags", {})
        ms = str(tags.get("maxspeed", "")).strip()
        out.append(OsmCamera(int(el["id"]), float(el["lat"]), float(el["lon"]),
                             int(ms) if ms.isdigit() else None, parse_direction(tags.get("direction"))))
    return out

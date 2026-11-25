"""Leitura/escrita dos CSVs. O de confirmados usa EXATAMENTE o cabeçalho do Pardal que o app já importa."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from .classify import Group

PARDAL_HEADER = ["lat", "lng", "kind", "limit_kmh", "source", "active", "end_lat", "end_lng", "direction_deg"]
PENDING_HEADER = ["status", "kind", "lat", "lng", "limit_kmh", "road", "km", "street", "city", "direction_text",
                  "families", "reasons", "evidence", "source_urls", "first_seen"]


def _fmt(v) -> str:
    return "" if v is None else (f"{v:.6f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))


def tag(group: Group) -> str:
    return "CENTRAL+" + "+".join(sorted(f.upper() for f in (group.tag_families or group.families)))


def confirmed_rows(groups: list[Group]) -> list[list[str]]:
    rows = []
    for g in groups:
        if g.status != "confirmed" or g.lat is None:
            continue
        rows.append([f"{g.lat:.6f}", f"{g.lng:.6f}", g.kind, _fmt(g.limit_kmh), tag(g), "1", "", "",
                     _fmt(round(g.bearing) if g.bearing is not None else None)])
    return sorted(rows)


def unofficial_rows(groups: list[Group]) -> list[list[str]]:
    """Pontos NÃO oficiais com coordenada estruturada. O nível (corroborado/não confirmado) o app deriva das etiquetas."""
    rows = []
    for g in groups:
        if g.status != "unofficial" or g.lat is None:
            continue
        rows.append([f"{g.lat:.6f}", f"{g.lng:.6f}", g.kind, _fmt(g.limit_kmh), tag(g), "1", "", "",
                     _fmt(round(g.bearing) if g.bearing is not None else None)])
    return sorted(rows)


def pending_rows(groups: list[Group]) -> list[list[str]]:
    rows = []
    for g in groups:
        if g.status == "confirmed" or (g.status == "unofficial" and not g.needs_review):
            continue
        m = g.members[0]
        rows.append([
            "pending", g.kind, _fmt(g.lat), _fmt(g.lng), _fmt(g.limit_kmh), m.road or "", _fmt(m.km), m.street or "",
            m.city or "", m.direction_text or "", "+".join(sorted(g.families)), " | ".join(g.reasons),
            " || ".join(dict.fromkeys(x.evidence for x in g.members if x.evidence))[:600],
            " ".join(dict.fromkeys(x.source_url for x in g.members)),
            min((x.first_seen for x in g.members if x.first_seen), default=""),
        ])
    return sorted(rows)


def to_csv(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8").lstrip("﻿"))))

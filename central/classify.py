"""Regras de confirmação e de nível de confiança. Conservadoras de propósito:

- confirmed  (OFICIAL CONFIRMADO): coordenada ESTRUTURADA (OpenStreetMap) + fonte oficial independente concordando.
- unofficial (NÃO OFICIAL): tem coordenada estruturada, mas sem fonte oficial concordando. O nível sai de level_of():
  corroborado se 2+ fontes diferentes concordam no local, senão não confirmado.
- pending: sem coordenada estruturada (só endereço geocodificado ou rodovia/km). Fica FORA do mapa do app.
Coordenada geocodificada de endereço nunca vira ponto no mapa sozinha. level_of() espelha o Confidence.kt do app."""
from __future__ import annotations

from dataclasses import dataclass, field

from .geo import directions_compatible, haversine_m, norm_name

DUP_RADIUS_M = 30.0          # mesmo ponto na base (e sentido compatível) = duplicata
CLUSTER_RADIUS_M = 80.0      # candidatos de fontes diferentes que descrevem o mesmo local
GEOCODE_AGREE_M = 150.0      # tolerância do endereço geocodificado frente ao ponto estruturado
OFFICIAL = {"prefeitura", "der-sp", "artesp", "dnit", "prf", "antt", "inmetro"}
IGNORED_FAMILIES = {"central"}   # etiqueta de transporte, não é fonte


def level_of(families) -> str:
    """OFFICIAL_CONFIRMED | UNOFFICIAL_CORROBORATED | UNCONFIRMED. Igual ao Confidence.kt do app (testado dos dois lados)."""
    tags = {f.strip().upper() for f in families if f and f.strip()} - {f.upper() for f in IGNORED_FAMILIES}
    if tags & {f.upper() for f in OFFICIAL}:
        return "OFFICIAL_CONFIRMED"
    independent = tags - {"PARDAL"}
    count = len(independent) if independent else min(len(tags), 1)
    return "UNOFFICIAL_CORROBORATED" if count >= 2 else "UNCONFIRMED"


@dataclass
class Cand:
    family: str                    # osm | prefeitura | der-sp | artesp | dnit | prf | antt | imprensa
    source_url: str
    evidence: str = ""
    kind: str = "UNKNOWN"
    lat: float | None = None
    lng: float | None = None
    coord_method: str | None = None  # "structured" (osm) | "geocode" | None
    limit_kmh: int | None = None
    bearing: float | None = None
    road: str | None = None
    km: float | None = None
    street: str | None = None
    city: str | None = None
    direction_text: str | None = None
    status_text: str | None = None
    first_seen: str = ""
    osm_id: int | None = None

    def key(self) -> str:
        loc = f"{self.lat:.5f},{self.lng:.5f}" if self.lat is not None else f"{self.road}|{self.km}|{self.street}"
        return f"{self.family}|{norm_name(loc)}|{norm_name(self.evidence)[:60]}"


@dataclass
class Group:
    members: list[Cand] = field(default_factory=list)
    status: str = "pending"
    reasons: list[str] = field(default_factory=list)
    lat: float | None = None
    lng: float | None = None
    limit_kmh: int | None = None
    bearing: float | None = None
    kind: str = "FIXED"
    tag_families: set = field(default_factory=set)   # fontes que concordam no local (base do nível)
    needs_review: bool = False

    @property
    def level(self) -> str:
        return level_of(self.tag_families or self.families)

    @property
    def families(self) -> set[str]:
        return {m.family for m in self.members}


def is_duplicate_of_base(c: Cand, base) -> bool:
    if c.lat is None or c.lng is None:
        return False
    for b in base:
        if haversine_m(c.lat, c.lng, b.lat, b.lng) <= DUP_RADIUS_M and directions_compatible(c.bearing, b.bearing):
            return True
    return False


def _near_base(c: Cand, base, radius: float) -> bool:
    return any(haversine_m(c.lat, c.lng, b.lat, b.lng) <= radius and directions_compatible(c.bearing, b.bearing) for b in base)


def _cluster(cands: list[Cand]) -> list[list[Cand]]:
    located = [c for c in cands if c.lat is not None]
    unlocated = [c for c in cands if c.lat is None]
    parent = list(range(len(located)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(located)):
        for j in range(i + 1, len(located)):
            a, b = located[i], located[j]
            if haversine_m(a.lat, a.lng, b.lat, b.lng) <= CLUSTER_RADIUS_M and directions_compatible(a.bearing, b.bearing):
                parent[find(i)] = find(j)
    groups: dict[int, list[Cand]] = {}
    for i, c in enumerate(located):
        groups.setdefault(find(i), []).append(c)
    out = list(groups.values())
    by_key: dict[str, list[Cand]] = {}
    for c in unlocated:
        k = f"{norm_name(c.road or '')}|{round(c.km, 1) if c.km is not None else ''}|{norm_name(c.street or '')}|{norm_name(c.city or '')}"
        by_key.setdefault(k, []).append(c)
    return out + list(by_key.values())


def classify(cands: list[Cand], base) -> tuple[list[Group], dict]:
    """Agrupa, descarta o que já está na base e decide confirmado x pendente. Devolve (grupos, estatísticas)."""
    stats = {"already_in_base": 0, "confirmed": 0, "unofficial": 0, "pending": 0}
    groups: list[Group] = []
    for members in _cluster(cands):
        structured = [m for m in members if m.coord_method == "structured"]
        if structured and all(is_duplicate_of_base(m, base) for m in structured):
            stats["already_in_base"] += 1  # o ponto já está na base; o texto só reforça, não duplica
            continue
        if not structured and any(m.lat is not None for m in members) and all(
            _near_base(m, base, GEOCODE_AGREE_M) for m in members if m.lat is not None
        ):
            stats["already_in_base"] += 1  # endereço geocodificado caiu junto de um radar já cadastrado
            continue
        g = Group(members=members)
        osm = structured[0] if structured else None
        non_osm_official = [m for m in members if m.family in OFFICIAL]
        if osm and non_osm_official:
            near = [m for m in non_osm_official
                    if m.coord_method == "geocode" and m.lat is not None
                    and haversine_m(osm.lat, osm.lng, m.lat, m.lng) <= GEOCODE_AGREE_M]
            if near:
                g.status = "confirmed"
                g.lat, g.lng, g.bearing = osm.lat, osm.lng, osm.bearing
                g.limit_kmh = next((m.limit_kmh for m in non_osm_official if m.limit_kmh), osm.limit_kmh)
                kinds = [m.kind for m in members if m.kind in ("FIXED", "SECTION", "RED_LIGHT")]
                g.kind = kinds[0] if kinds else "FIXED"
        if g.status != "confirmed":
            if osm and not non_osm_official:
                g.reasons.append("apenas OpenStreetMap (colaborativo), sem fonte oficial concordando")
            elif osm and non_osm_official:
                g.reasons.append("fonte oficial não localizada junto ao ponto do OpenStreetMap")
            else:
                if not any(m.lat is not None for m in members):
                    g.reasons.append("sem coordenadas (apenas rodovia/km ou rua): precisa de fonte com localização")
                else:
                    g.reasons.append("coordenada obtida só por endereço (erro possível de centenas de metros)")
                if not (g.families & OFFICIAL):
                    g.reasons.append("nenhuma fonte oficial")
            first = osm or members[0]
            g.lat, g.lng, g.bearing = first.lat, first.lng, first.bearing
            g.limit_kmh = next((m.limit_kmh for m in members if m.limit_kmh), None)
            kinds = [m.kind for m in members if m.kind in ("FIXED", "SECTION", "RED_LIGHT")]
            g.kind = kinds[0] if kinds else "FIXED"
            if osm:
                g.status = "unofficial"
                agree = [m for m in members if m is not osm and m.lat is not None
                         and haversine_m(osm.lat, osm.lng, m.lat, m.lng) <= GEOCODE_AGREE_M]
                g.tag_families = {"osm"} | {m.family for m in agree}
                g.needs_review = any(m.family in OFFICIAL and m not in agree for m in members)
        else:
            g.tag_families = set(g.families)
        stats[g.status] += 1
        groups.append(g)
    return groups, stats

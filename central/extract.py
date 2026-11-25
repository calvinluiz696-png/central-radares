"""Extração por IA com defesa contra invenção: cada achado precisa trazer a FRASE LITERAL da página, e o programa
confere se ela existe no texto. Número (limite, km) que não aparece na frase é descartado. A IA nunca fornece coordenadas."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

SYSTEM = (
    "Você extrai dados de radares de trânsito de textos públicos em português do Brasil. "
    "Regras absolutas: (1) use SOMENTE o que está escrito no texto; (2) nunca invente, complete ou deduza "
    "endereços, quilômetros, limites ou coordenadas; (3) não escreva latitude nem longitude; (4) se o texto não "
    "trouxer radar, responda []; (5) responda apenas JSON válido, sem comentários."
)

ALLOWED_KINDS = {"FIXED", "SECTION", "RED_LIGHT", "UNKNOWN"}

PROMPT_TEMPLATE = """Cidade/região de referência da página: {city}
Leia o texto e liste cada radar (fiscalização eletrônica de velocidade ou avanço de sinal) citado COM localização.
Responda uma lista JSON; cada item com exatamente estes campos:
- "kind": "FIXED" (radar fixo), "SECTION" (velocidade média/trecho), "RED_LIGHT" (sinal vermelho) ou "UNKNOWN"
- "street": nome da rua/avenida como está no texto, ou null
- "road": sigla da rodovia (ex.: "SP-075"), ou null
- "km": quilômetro como número, ou null
- "direction_text": sentido como está no texto (ex.: "sentido capital"), ou null
- "city": município citado, ou null
- "limit_kmh": limite de velocidade em km/h se o texto disser, senão null
- "status_text": situação dita no texto (ex.: "em operação", "será instalado", "desativado"), ou null
- "evidence": cópia EXATA, palavra por palavra, da frase do texto que sustenta o item (mínimo 15 caracteres)
Se um dado não estiver no texto, use null. Não repita itens.

TEXTO:
\"\"\"
{text}
\"\"\"
"""


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().casefold()


def numbers_in(s: str) -> set[float]:
    out = set()
    for m in re.finditer(r"\d+(?:[.,]\d+)?", s):
        raw = m.group(0)
        try:
            out.add(float(raw.replace(".", "").replace(",", ".")) if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw) else float(raw.replace(",", ".")))
        except ValueError:
            pass
        try:
            out.add(float(raw.replace(",", ".")))
        except ValueError:
            pass
    return out


@dataclass
class Extracted:
    kind: str
    street: str | None
    road: str | None
    km: float | None
    direction_text: str | None
    city: str | None
    limit_kmh: int | None
    status_text: str | None
    evidence: str
    notes: list[str] = field(default_factory=list)


def parse_json_loose(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\[.*\]", t, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() and v.strip().lower() not in ("null", "none") else None


def validate(raw, page_text: str) -> tuple[list[Extracted], list[str]]:
    """Devolve (válidos, motivos de rejeição). Rejeita tudo que não puder ser provado pelo texto da página."""
    rejected: list[str] = []
    items = raw.get("radares", raw.get("items", [])) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return [], ["resposta não é uma lista"]
    haystack = norm_text(page_text)
    good: list[Extracted] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            rejected.append(f"item {i}: formato inválido")
            continue
        evidence = _str(it.get("evidence"))
        if not evidence or len(evidence) < 15:
            rejected.append(f"item {i}: sem frase de evidência")
            continue
        if norm_text(evidence) not in haystack:
            rejected.append(f"item {i}: a frase de evidência NÃO existe no texto (possível invenção)")
            continue
        ev_numbers = numbers_in(evidence)
        notes: list[str] = []
        limit = it.get("limit_kmh")
        if isinstance(limit, str) and limit.strip().isdigit():
            limit = int(limit.strip())
        if isinstance(limit, bool) or not isinstance(limit, int) or not (20 <= limit <= 130) or float(limit) not in ev_numbers:
            if limit not in (None, ""):
                notes.append("limite descartado: não confere com a frase de evidência")
            limit = None
        km = it.get("km")
        if isinstance(km, str):
            try:
                km = float(km.replace(",", "."))
            except ValueError:
                km = None
        if isinstance(km, bool) or not isinstance(km, (int, float)):
            km = None
        elif float(km) not in ev_numbers and round(float(km), 3) not in ev_numbers:
            notes.append("km descartado: não confere com a frase de evidência")
            km = None
        street, road = _str(it.get("street")), _str(it.get("road"))
        if street and norm_text(street) not in haystack:
            notes.append("rua descartada: não aparece no texto")
            street = None
        if road and norm_text(road).replace(" ", "") not in haystack.replace(" ", ""):
            notes.append("rodovia descartada: não aparece no texto")
            road = None
        if not (street or (road and km is not None)):
            rejected.append(f"item {i}: sem localização comprovada (rua ou rodovia+km)")
            continue
        kind = (_str(it.get("kind")) or "UNKNOWN").upper()
        if kind not in ALLOWED_KINDS:
            kind = "UNKNOWN"
        good.append(Extracted(kind, street, road, float(km) if km is not None else None, _str(it.get("direction_text")),
                              _str(it.get("city")), limit, _str(it.get("status_text")), evidence, notes))
    return good, rejected


def chunk_text(text: str, size: int = 9000, overlap: int = 400) -> list[str]:
    if len(text) <= size:
        return [text]
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        if i + size >= len(text):
            break
        i += size - overlap
    return out


def build_prompt(text: str, city: str) -> str:
    return PROMPT_TEMPLATE.format(city=city or "não informada", text=text)

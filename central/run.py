"""Orquestra a Central: coleta -> (IA) extração -> geocodificação -> classificação -> CSVs.

Uso:  python -m central.run [--no-ai] [--no-network-osm]
Nunca apaga radares já confirmados. Se todas as IAs estiverem sem cota, a etapa de IA é pulada e o resto segue.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import classify as cl
from . import csvio, extract, http
from .geo import Geocoder, in_box
from .providers import AllProvidersUnavailable, Rotator, load_config
from .quota import QuotaBook
from .sources import PARDAL_URL, BasePoint, content_hash, fetch_osm_cameras, html_to_text, parse_pardal

ROOT = Path(__file__).resolve().parent.parent
LICENSE_NOTE = ("Dados derivados do projeto Pardal e do OpenStreetMap sob a Open Database License (ODbL) 1.0. "
                "Atribuição obrigatória; redistribuição da base derivada deve manter a mesma licença.")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Deps:
    """Pontos de troca para testes (rede e IA falsas)."""

    def __init__(self, fetch_text=None, fetch_pardal=None, fetch_osm=None, rotator: Rotator | None = None, geocoder: Geocoder | None = None):
        self.fetch_text = fetch_text or (lambda url: http.get_text(url, timeout=40))
        self.fetch_pardal = fetch_pardal or (lambda: http.get_text(PARDAL_URL, timeout=60))
        self.fetch_osm = fetch_osm or fetch_osm_cameras
        self.rotator = rotator
        self.geocoder = geocoder


def run(root: Path, deps: Deps, use_ai: bool = True) -> dict:
    cfg_dir, data_dir, state_dir = root / "config", root / "data", root / "state"
    data_dir.mkdir(exist_ok=True)
    state_dir.mkdir(exist_ok=True)
    cities = json.loads((cfg_dir / "cities.json").read_text(encoding="utf-8"))
    box = tuple(cities["region_box"])  # (sul, oeste, norte, leste)
    sources = json.loads((cfg_dir / "sources.json").read_text(encoding="utf-8"))["pages"]
    report: dict = {"started": _now(), "status": "ok", "notes": [], "pages": [], "ai": {}}
    log_lines: list[dict] = []

    # 1) Base atual (Pardal) — se falhar, segue só com o que já existe (nunca derruba os dados).
    base = []
    try:
        base = parse_pardal(deps.fetch_pardal())
        report["pardal_points"] = len(base)
    except http.HttpError as e:
        report["status"] = "partial"
        report["notes"].append(f"Pardal indisponível ({e}); comparação feita sem a base.")
    for r in csvio.read_rows(data_dir / "confirmados.csv"):
        try:
            base.append(BasePoint(float(r["lat"]), float(r["lng"]), r["kind"], float(r["direction_deg"]) if r["direction_deg"] else None))
        except (KeyError, ValueError):
            pass

    # 2) OpenStreetMap (estruturado, com coordenada).
    cands_path = state_dir / "candidates.json"
    stored: dict[str, dict] = {}
    if cands_path.exists():
        try:
            stored = {c["_k"]: c for c in json.loads(cands_path.read_text(encoding="utf-8"))}
        except (ValueError, KeyError):
            stored = {}
    osm_cands: list[cl.Cand] = []
    osm_ok = False
    try:
        for cam in deps.fetch_osm(box):
            osm_cands.append(cl.Cand("osm", f"https://www.openstreetmap.org/node/{cam.osm_id}", f"OSM node {cam.osm_id}", "FIXED",
                                     cam.lat, cam.lng, "structured", cam.limit_kmh, cam.bearing, osm_id=cam.osm_id, first_seen=_now()[:10]))
        report["osm_cameras"] = len(osm_cands)
        osm_ok = True
    except (http.HttpError, ValueError) as e:
        report["status"] = "partial"
        report["notes"].append(f"OpenStreetMap indisponível ({e}).")

    # 3) Páginas públicas -> texto -> IA (só se a página mudou).
    page_state_path = state_dir / "pages.json"
    page_state: dict[str, str] = json.loads(page_state_path.read_text(encoding="utf-8")) if page_state_path.exists() else {}
    book = QuotaBook.load(state_dir / "quota.json")
    rotator = deps.rotator
    if use_ai and rotator is None:
        order, configs = load_config(cfg_dir / "providers.json")
        rotator = Rotator(order, configs, book, log=log_lines.append)
    ai_paused = not use_ai
    for page in sources:
        entry = {"id": page["id"], "url": page["url"], "family": page["family"], "result": ""}
        report["pages"].append(entry)
        try:
            text = html_to_text(deps.fetch_text(page["url"]))
        except http.HttpError as e:
            entry["result"] = f"falha ao baixar ({e})"
            report["status"] = "partial"
            continue
        h = content_hash(text)
        if page_state.get(page["id"]) == h:
            entry["result"] = "sem mudança (IA não consultada)"
            continue
        if ai_paused:
            entry["result"] = "IA pausada: será reprocessada na próxima execução"
            continue
        found, rejected_all = [], []
        try:
            for chunk in extract.chunk_text(text):
                raw_text, used = rotator.complete(extract.SYSTEM, extract.build_prompt(chunk, page.get("city", "")))
                try:
                    good, rejected = extract.validate(extract.parse_json_loose(raw_text), text)
                except ValueError:
                    good, rejected = [], [f"{used}: resposta não era JSON"]
                found += good
                rejected_all += rejected
        except AllProvidersUnavailable as e:
            ai_paused = True
            report["status"] = "partial"
            report["ai"]["paused_reason"] = str(e)
            entry["result"] = "IA pausada (sem cota/chave): retoma quando renovar"
            continue
        page_state[page["id"]] = h  # só marca como processada se a IA realmente leu
        entry["result"] = f"{len(found)} achado(s) válido(s), {len(rejected_all)} rejeitado(s)"
        entry["rejected"] = rejected_all[:10]
        for f in found:
            c = cl.Cand(page["family"], page["url"], f.evidence, f.kind, None, None, None, f.limit_kmh, None, f.road, f.km,
                        f.street, f.city or page.get("city"), f.direction_text, f.status_text, _now()[:10])
            old = stored.get(c.key())
            if old:
                c.first_seen = old.get("first_seen", c.first_seen)
            stored[c.key()] = {**asdict(c), "_k": c.key()}

    # 4) Geocodifica endereços (só os que ainda não têm coordenada).
    geocoder = deps.geocoder or Geocoder(state_dir / "geocache.json")
    text_cands: list[cl.Cand] = []
    for rec in stored.values():
        c = cl.Cand(**{k: v for k, v in rec.items() if k != "_k"})
        if c.lat is None and c.street and c.city:
            pos = geocoder.lookup(c.street, c.city, box)
            if pos:
                c.lat, c.lng, c.coord_method = pos[0], pos[1], "geocode"
                rec.update(lat=c.lat, lng=c.lng, coord_method="geocode")
        text_cands.append(c)
    geocoder.save()

    # 5) Classifica e escreve. Nunca remove confirmados anteriores.
    groups, stats = cl.classify(osm_cands + text_cands, base)
    old_confirmed = {tuple(r[:3]): r for r in (
        [r["lat"], r["lng"], r["kind"], r["limit_kmh"], r["source"], r["active"], r["end_lat"], r["end_lng"], r["direction_deg"]]
        for r in csvio.read_rows(data_dir / "confirmados.csv"))}
    new_rows = {tuple(r[:3]): r for r in csvio.confirmed_rows(groups)}
    merged = sorted({**old_confirmed, **new_rows}.values())
    (data_dir / "confirmados.csv").write_text(csvio.to_csv(csvio.PARDAL_HEADER, merged), encoding="utf-8")
    pending = csvio.pending_rows(groups)
    unofficial = csvio.unofficial_rows(groups)
    if osm_ok:  # sem o OpenStreetMap as listas ficariam incompletas: mantém as anteriores em vez de esvaziá-las
        (data_dir / "pendentes.csv").write_text(csvio.to_csv(csvio.PENDING_HEADER, pending), encoding="utf-8")
        (data_dir / "nao_oficiais.csv").write_text(csvio.to_csv(csvio.PARDAL_HEADER, unofficial), encoding="utf-8")
    else:
        report["notes"].append("pendentes.csv e nao_oficiais.csv mantidos como estavam (OpenStreetMap indisponível nesta execução).")
    levels: dict[str, int] = {}
    for g in groups:
        if g.status == "unofficial":
            levels[g.level] = levels.get(g.level, 0) + 1
    cands_path.write_text(json.dumps(sorted(stored.values(), key=lambda d: d["_k"]), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    page_state_path.write_text(json.dumps(page_state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    book.save()

    report.update(stats=stats, confirmed_total=len(merged), pending_total=len(pending), unofficial_total=len(unofficial), unofficial_levels=levels, finished=_now(),
                  license=LICENSE_NOTE, ai={**report["ai"], "calls": getattr(rotator, "usage", {}) if rotator else {}})
    (data_dir / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (state_dir / "ultima-execucao.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in log_lines) + ("\n" if log_lines else ""), encoding="utf-8")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true", help="só coleta determinística (sem chamar nenhuma IA)")
    args = ap.parse_args(argv)
    rep = run(ROOT, Deps(), use_ai=not args.no_ai)
    print(json.dumps({k: rep[k] for k in ("status", "stats", "confirmed_total", "unofficial_total", "pending_total", "notes", "ai")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

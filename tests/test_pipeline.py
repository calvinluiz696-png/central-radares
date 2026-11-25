import json
import shutil
import tempfile
import unittest
from pathlib import Path

from central import csvio, http
from central.classify import Cand, classify
from central.geo import Geocoder, angle_diff, directions_compatible, haversine_m, parse_direction
from central.providers import ProviderConfig, Rotator
from central.quota import QuotaBook
from central.run import Deps, run
from central.sources import BasePoint, OsmCamera, parse_pardal

ROOT = Path(__file__).resolve().parent.parent
PARDAL = """lat,lng,kind,limit_kmh,source,active,end_lat,end_lng,direction_deg
-23.18570,-46.89780,FIXED,60,DNIT+OSM,1,,,90
-23.20000,-46.90000,FIXED,40,OSM,0,,,
bad,row,FIXED,,,1,,,
"""
P = (-23.19000, -46.88000)          # radar novo (OSM)
P_GEOCODED = (-23.19060, -46.88000)  # ~67 m do P (endereço geocodificado)
Q = (-23.21000, -46.87000)          # só OSM
PAGE = "<p>Os radares da avenida Exemplo, em Jundiaí, passam a operar na segunda-feira. O limite é de 50 km/h.</p><p>A SP-075, km 1,200, sentido sul, terá radar fixo.</p>"
AI_REPLY = json.dumps([
    {"kind": "FIXED", "street": "avenida Exemplo", "road": None, "km": None, "direction_text": None, "city": "Jundiaí",
     "limit_kmh": 50, "status_text": "passam a operar",
     "evidence": "Os radares da avenida Exemplo, em Jundiaí, passam a operar na segunda-feira. O limite é de 50 km/h."},
    {"kind": "FIXED", "street": None, "road": "SP-075", "km": 1.2, "direction_text": "sentido sul", "city": None,
     "limit_kmh": None, "status_text": None, "evidence": "A SP-075, km 1,200, sentido sul, terá radar fixo."},
])


class FakeGeo(Geocoder):
    def __init__(self):
        super().__init__(None, fetch=lambda u: "[]", sleep=lambda s: None)
        self.calls = 0

    def lookup(self, street, city, box):
        self.calls += 1
        return P_GEOCODED if "Exemplo" in street else None


def make_root(tmp: Path) -> Path:
    shutil.copytree(ROOT / "config", tmp / "config")
    cfg = json.loads((tmp / "config" / "sources.json").read_text())
    cfg["pages"] = [{"id": "p1", "family": "prefeitura", "city": "Jundiaí", "url": "http://x/p1"}]
    (tmp / "config" / "sources.json").write_text(json.dumps(cfg))
    return tmp


def rotator(reply=AI_REPLY, fail=False, counter=None):
    cfgs = {"gemini": ProviderConfig("gemini", True, "m", "u", "K", 0)}

    def call(cfg, key, system, prompt):
        if counter is not None:
            counter.append(1)
        if fail:
            raise http.HttpError(429, "quota")
        return reply
    return Rotator(["gemini"], cfgs, QuotaBook(), {"gemini": call}, {"K": "x"})


def deps(rot, osm=None):
    cams = osm if osm is not None else [OsmCamera(1, -23.18570, -46.89780, 60, 90.0), OsmCamera(2, *P, 50, None), OsmCamera(3, *Q, None, None)]
    return Deps(fetch_text=lambda u: PAGE, fetch_pardal=lambda: PARDAL, fetch_osm=lambda box: cams, rotator=rot, geocoder=FakeGeo())


class GeoTest(unittest.TestCase):
    def test_distance_and_direction(self):
        self.assertAlmostEqual(haversine_m(*P, *P_GEOCODED), 66.7, delta=2)
        self.assertEqual(angle_diff(350, 10), 20)
        self.assertTrue(directions_compatible(None, 90))
        self.assertFalse(directions_compatible(90, 270))  # sentidos opostos = radares diferentes
        self.assertEqual(parse_direction("NE"), 45.0)
        self.assertIsNone(parse_direction("forward"))
        self.assertEqual(parse_direction("180"), 180.0)

    def test_geocoder_rejects_other_city_and_outside_box(self):
        box = (-23.4, -47.3, -22.7, -46.6)
        reply = json.dumps([{"lat": "-23.19", "lon": "-46.88", "address": {"city": "Campinas"}}])
        g = Geocoder(None, fetch=lambda u: reply, sleep=lambda s: None)
        self.assertIsNone(g.lookup("Rua A", "Jundiaí", box))
        reply = json.dumps([{"lat": "-10", "lon": "-46.88", "address": {"city": "Jundiaí"}}])
        g = Geocoder(None, fetch=lambda u: reply, sleep=lambda s: None)
        self.assertIsNone(g.lookup("Rua A", "Jundiaí", box))
        reply = json.dumps([{"lat": "-23.19", "lon": "-46.88", "address": {"city": "Jundiaí"}}])
        g = Geocoder(None, fetch=lambda u: reply, sleep=lambda s: None)
        self.assertEqual(g.lookup("Rua A", "Jundiaí", box), (-23.19, -46.88))


class ClassifyTest(unittest.TestCase):
    base = [BasePoint(-23.1857, -46.8978, "FIXED", 90.0)]

    def test_osm_already_in_base_is_not_new(self):
        c = Cand("osm", "u", "n", coord_method="structured", lat=-23.18572, lng=-46.89781)
        groups, stats = classify([c], self.base)
        self.assertEqual((groups, stats["already_in_base"]), ([], 1))

    def test_opposite_direction_next_to_base_is_a_different_radar(self):
        c = Cand("osm", "u", "n", coord_method="structured", lat=-23.18572, lng=-46.89781, bearing=270.0)
        groups, stats = classify([c], self.base)
        self.assertEqual(stats["already_in_base"], 0)
        self.assertEqual(len(groups), 1)

    def test_osm_alone_is_unofficial_unconfirmed(self):
        groups, stats = classify([Cand("osm", "u", "n", coord_method="structured", lat=-23.3, lng=-46.7)], self.base)
        self.assertEqual(groups[0].status, "unofficial")
        self.assertEqual(groups[0].level, "UNCONFIRMED")
        self.assertEqual(stats["unofficial"], 1)
        self.assertIn("OpenStreetMap", groups[0].reasons[0])

    def test_osm_plus_official_confirms_with_osm_coordinates(self):
        osm = Cand("osm", "u", "n", coord_method="structured", lat=P[0], lng=P[1], limit_kmh=40)
        txt = Cand("prefeitura", "u2", "e", coord_method="geocode", lat=P_GEOCODED[0], lng=P_GEOCODED[1], limit_kmh=50)
        groups, stats = classify([osm, txt], self.base)
        g = groups[0]
        self.assertEqual(g.status, "confirmed")
        self.assertEqual((g.lat, g.lng, g.limit_kmh), (P[0], P[1], 50))

    def test_press_never_confirms_but_corroborates(self):
        osm = Cand("osm", "u", "n", coord_method="structured", lat=P[0], lng=P[1])
        txt = Cand("imprensa", "u2", "e", coord_method="geocode", lat=P_GEOCODED[0], lng=P_GEOCODED[1])
        g = classify([osm, txt], self.base)[0][0]
        self.assertEqual(g.status, "unofficial")          # nunca "confirmed"
        self.assertEqual(g.level, "UNOFFICIAL_CORROBORATED")
        self.assertEqual((g.lat, g.lng), (P[0], P[1]))    # coordenada é a estruturada, nunca a geocodificada

    def test_press_geocode_only_stays_pending_off_the_map(self):
        txt = Cand("imprensa", "u2", "e", coord_method="geocode", lat=P_GEOCODED[0], lng=P_GEOCODED[1])
        self.assertEqual(classify([txt], self.base)[0][0].status, "pending")

    def test_official_text_close_to_osm_confirms(self):
        osm = Cand("osm", "u", "n", coord_method="structured", lat=P[0], lng=P[1])
        far = Cand("prefeitura", "u2", "e", coord_method="geocode", lat=P[0] + 0.0007, lng=P[1])  # ~78 m: mesmo grupo
        g = classify([osm, far], self.base)[0][0]
        self.assertEqual(g.status, "confirmed")  # dentro de 150 m do OSM = concorda (e é oficial)

    def test_level_of_matches_the_app_rules(self):
        from central.classify import level_of
        cases = [
            (["prefeitura"], "OFFICIAL_CONFIRMED"), (["DNIT", "osm"], "OFFICIAL_CONFIRMED"), (["inmetro"], "OFFICIAL_CONFIRMED"),
            (["osm", "forum"], "UNOFFICIAL_CORROBORATED"), (["osm", "pardal", "imprensa"], "UNOFFICIAL_CORROBORATED"),
            (["osm"], "UNCONFIRMED"), ([], "UNCONFIRMED"), (["pardal"], "UNCONFIRMED"),
            (["osm", "central"], "UNCONFIRMED"), (["osm", "forum", "central"], "UNOFFICIAL_CORROBORATED"),
        ]
        for fam, want in cases:
            self.assertEqual(level_of(fam), want, fam)

    def test_geocode_only_official_stays_pending(self):
        txt = Cand("prefeitura", "u2", "e", coord_method="geocode", lat=P_GEOCODED[0], lng=P_GEOCODED[1])
        g = classify([txt], self.base)[0][0]
        self.assertEqual(g.status, "pending")
        self.assertIn("endereço", " ".join(g.reasons))

    def test_km_only_is_pending_without_coordinates(self):
        g = classify([Cand("der-sp", "u", "e", road="SP-075", km=1.2)], self.base)[0][0]
        self.assertEqual(g.status, "pending")
        self.assertIsNone(g.lat)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_run(self):
        rep = run(self.root, deps(rotator()))
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["stats"], {"already_in_base": 1, "confirmed": 1, "unofficial": 1, "pending": 1})
        text = (self.root / "data" / "confirmados.csv").read_text()
        self.assertEqual(text.splitlines()[0], ",".join(csvio.PARDAL_HEADER))
        pts = parse_pardal(text)  # o mesmo leitor do formato que o app aceita
        self.assertEqual(len(pts), 1)
        self.assertAlmostEqual(pts[0].lat, P[0], 5)
        self.assertEqual(pts[0].limit_kmh, 50)
        self.assertIn("CENTRAL+OSM+PREFEITURA", text)
        pending = (self.root / "data" / "pendentes.csv").read_text()
        self.assertIn("SP-075", pending)
        self.assertNotIn(f"{P[0]:.5f}", pending)  # o confirmado não vaza para pendentes
        unof = (self.root / "data" / "nao_oficiais.csv").read_text()
        self.assertEqual(unof.splitlines()[0], ",".join(csvio.PARDAL_HEADER))  # mesmo formato que o app lê
        self.assertEqual(len(parse_pardal(unof)), 1)
        self.assertIn("CENTRAL+OSM", unof)
        self.assertNotIn("PREFEITURA", unof)
        self.assertNotIn(f"{P[0]:.5f}", unof)
        self.assertEqual(rep["unofficial_levels"], {"UNCONFIRMED": 1})

    def test_unchanged_page_does_not_call_ai_again(self):
        calls: list = []
        run(self.root, deps(rotator(counter=calls)))
        first = len(calls)
        self.assertGreater(first, 0)
        run(self.root, deps(rotator(counter=calls)))
        self.assertEqual(len(calls), first)

    def test_ai_exhausted_pauses_but_keeps_data_and_retries_later(self):
        run(self.root, deps(rotator()))
        before = (self.root / "data" / "confirmados.csv").read_text()
        # página nova + todas as IAs sem cota
        d = deps(rotator(fail=True))
        d.fetch_text = lambda u: PAGE + "<p>Texto novo.</p>"
        rep = run(self.root, d)
        self.assertEqual(rep["status"], "partial")
        self.assertIn("paused_reason", rep["ai"])
        self.assertEqual((self.root / "data" / "confirmados.csv").read_text(), before)  # nada perdido
        state = json.loads((self.root / "state" / "pages.json").read_text())
        from central.sources import content_hash, html_to_text
        self.assertEqual(state["p1"], content_hash(html_to_text(PAGE)))  # continua com o hash da página JÁ processada
        # IA volta: a página é reprocessada
        calls: list = []
        d2 = deps(rotator(counter=calls))
        d2.fetch_text = lambda u: PAGE + "<p>Texto novo.</p>"
        run(self.root, d2)
        self.assertGreater(len(calls), 0)

    def test_confirmed_is_never_deleted_when_sources_vanish(self):
        run(self.root, deps(rotator()))
        run(self.root, deps(rotator(), osm=[]))  # OSM não devolve mais nada
        rows = csvio.read_rows(self.root / "data" / "confirmados.csv")
        self.assertEqual(len(rows), 1)

    def test_invented_ai_answer_creates_nothing(self):
        fake = json.dumps([{"kind": "FIXED", "street": "avenida Fantasma", "road": None, "km": None, "direction_text": None,
                            "city": "Jundiaí", "limit_kmh": 80, "status_text": None, "evidence": "Radar na avenida Fantasma com 80 km/h de limite"}])
        rep = run(self.root, deps(rotator(reply=fake), osm=[]))
        self.assertEqual(rep["pending_total"], 0)
        self.assertEqual(rep["confirmed_total"], 0)
        self.assertTrue(any("NÃO existe" in r for r in rep["pages"][0]["rejected"]))

    def test_no_ai_mode_still_produces_osm_unofficial(self):
        rep = run(self.root, deps(None), use_ai=False)
        self.assertEqual(rep["stats"]["unofficial"], 2)
        self.assertEqual(rep["stats"]["pending"], 0)
        self.assertEqual(rep["stats"]["confirmed"], 0)

    def test_osm_down_keeps_previous_pending_list(self):
        run(self.root, deps(rotator()))
        before = (self.root / "data" / "pendentes.csv").read_text()
        before_unof = (self.root / "data" / "nao_oficiais.csv").read_text()
        d = deps(rotator())

        def boom(box):
            raise http.HttpError(0, "sem rede")
        d.fetch_osm = boom
        rep = run(self.root, d)
        self.assertEqual(rep["status"], "partial")
        self.assertEqual((self.root / "data" / "pendentes.csv").read_text(), before)
        self.assertEqual((self.root / "data" / "nao_oficiais.csv").read_text(), before_unof)

    def test_pardal_down_marks_partial_and_continues(self):
        d = deps(rotator())

        def boom():
            raise http.HttpError(0, "sem rede")
        d.fetch_pardal = boom
        rep = run(self.root, d)
        self.assertEqual(rep["status"], "partial")
        self.assertIn("confirmados.csv", [p.name for p in (self.root / "data").iterdir()])


if __name__ == "__main__":
    unittest.main()

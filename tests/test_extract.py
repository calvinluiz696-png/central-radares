import unittest

from central import extract
from central.sources import html_to_text

PAGE = """Os radares da avenida Antonio Segre, em Jundiaí, passam a operar na segunda-feira. O limite é de 50 km/h.
A SP-075, km 1,200, sentido sul, também terá radar fixo."""


def item(**kw):
    base = {"kind": "FIXED", "street": None, "road": None, "km": None, "direction_text": None, "city": "Jundiaí",
            "limit_kmh": None, "status_text": None, "evidence": ""}
    base.update(kw)
    return base


class ExtractTest(unittest.TestCase):
    def test_valid_item_passes(self):
        ev = "Os radares da avenida Antonio Segre, em Jundiaí, passam a operar na segunda-feira. O limite é de 50 km/h."
        good, rej = extract.validate([item(street="avenida Antonio Segre", limit_kmh=50, evidence=ev)], PAGE)
        self.assertEqual(len(good), 1)
        self.assertEqual(good[0].limit_kmh, 50)
        self.assertEqual(rej, [])

    def test_invented_evidence_is_rejected(self):
        good, rej = extract.validate([item(street="avenida Antonio Segre", evidence="Haverá radar na avenida Antonio Segre com limite de 60 km/h")], PAGE)
        self.assertEqual(good, [])
        self.assertIn("NÃO existe", rej[0])

    def test_limit_not_in_evidence_is_dropped_not_kept(self):
        ev = "Os radares da avenida Antonio Segre, em Jundiaí, passam a operar na segunda-feira."
        good, _ = extract.validate([item(street="avenida Antonio Segre", limit_kmh=60, evidence=ev)], PAGE)
        self.assertEqual(len(good), 1)
        self.assertIsNone(good[0].limit_kmh)
        self.assertTrue(any("limite descartado" in n for n in good[0].notes))

    def test_km_with_decimal_comma_matches(self):
        ev = "A SP-075, km 1,200, sentido sul, também terá radar fixo."
        good, _ = extract.validate([item(road="SP-075", km=1.2, evidence=ev)], PAGE)
        self.assertEqual(len(good), 1)
        self.assertEqual(good[0].km, 1.2)

    def test_km_not_in_evidence_drops_candidate_without_location(self):
        ev = "A SP-075, km 1,200, sentido sul, também terá radar fixo."
        good, rej = extract.validate([item(road="SP-075", km=99, evidence=ev)], PAGE)
        self.assertEqual(good, [])
        self.assertIn("sem localização", rej[0])

    def test_no_location_rejected(self):
        ev = "Os radares da avenida Antonio Segre, em Jundiaí, passam a operar na segunda-feira."
        good, rej = extract.validate([item(evidence=ev)], PAGE)
        self.assertEqual(good, [])

    def test_street_not_in_text_is_dropped(self):
        ev = "Os radares da avenida Antonio Segre, em Jundiaí, passam a operar na segunda-feira."
        good, rej = extract.validate([item(street="rua Inventada", evidence=ev)], PAGE)
        self.assertEqual(good, [])

    def test_coordinates_from_ai_are_ignored(self):
        ev = "Os radares da avenida Antonio Segre, em Jundiaí, passam a operar na segunda-feira."
        good, _ = extract.validate([{**item(street="avenida Antonio Segre", evidence=ev), "lat": -23.1, "lng": -46.9}], PAGE)
        self.assertFalse(hasattr(good[0], "lat"))

    def test_whitespace_and_quote_differences_tolerated(self):
        good, _ = extract.validate([item(street="avenida Antonio Segre", evidence="Os  radares da\navenida Antonio Segre, em Jundiaí, passam a operar")], PAGE)
        self.assertEqual(len(good), 1)

    def test_json_in_code_fence(self):
        self.assertEqual(extract.parse_json_loose('```json\n[{"a":1}]\n```'), [{"a": 1}])
        self.assertEqual(extract.parse_json_loose('Aqui está: [{"a":1}] fim'), [{"a": 1}])

    def test_non_list_rejected(self):
        self.assertEqual(extract.validate({"x": 1}, PAGE)[0], [])

    def test_html_to_text_drops_scripts(self):
        t = html_to_text("<html><script>var a=1</script><p>Radar na <b>Rua A</b></p><style>p{}</style></html>")
        self.assertIn("Radar na Rua A", t)
        self.assertNotIn("var a", t)

    def test_chunking_overlaps(self):
        chunks = extract.chunk_text("x" * 20000, 9000, 400)
        self.assertGreater(len(chunks), 2)
        self.assertEqual(extract.chunk_text("curto"), ["curto"])


if __name__ == "__main__":
    unittest.main()

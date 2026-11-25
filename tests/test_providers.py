import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from central import http
from central.providers import AllProvidersUnavailable, ProviderConfig, Rotator
from central.quota import QuotaBook

NOW = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)


def cfgs(cap=0):
    return {n: ProviderConfig(n, True, "m", "u", f"{n.upper()}_KEY", cap) for n in ("gemini", "groq", "cloudflare", "mistral")}


ORDER = ["gemini", "groq", "cloudflare", "mistral"]
ENV = {"GEMINI_KEY": "x", "GROQ_KEY": "x", "CLOUDFLARE_KEY": "x", "MISTRAL_KEY": "x"}


def caller(results):
    def f(cfg, key, system, prompt):
        r = results[cfg.name]
        if isinstance(r, Exception):
            raise r
        return r
    return f


class RotatorTest(unittest.TestCase):
    def make(self, results, env=ENV, cap=0, book=None):
        book = book or QuotaBook()
        callers = {n: caller(results) for n in ORDER}
        return Rotator(ORDER, cfgs(cap), book, callers, env), book

    def test_primary_is_used_first(self):
        r, _ = self.make({n: f"resp-{n}" for n in ORDER})
        self.assertEqual(r.complete("s", "p", NOW), ("resp-gemini", "gemini"))

    def test_rate_limit_falls_to_next_and_primary_pauses(self):
        res = {n: f"resp-{n}" for n in ORDER}
        res["gemini"] = http.HttpError(429, "quota")
        r, book = self.make(res)
        text, who = r.complete("s", "p", NOW)
        self.assertEqual(who, "groq")
        # 429 sem Retry-After = cota diária: pausa até a virada UTC
        self.assertEqual(book.state("gemini").cooldown_until, datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc).isoformat())
        # segunda chamada nem tenta a Gemini
        self.assertEqual(r.complete("s", "p", NOW + timedelta(minutes=1))[1], "groq")

    def test_primary_returns_when_quota_renews(self):
        res = {n: f"resp-{n}" for n in ORDER}
        res["gemini"] = http.HttpError(429, "quota")
        r, book = self.make(res)
        r.complete("s", "p", NOW)
        res["gemini"] = "resp-gemini"  # cota renovou
        self.assertEqual(r.complete("s", "p", NOW + timedelta(days=1))[1], "gemini")

    def test_retry_after_short_wait(self):
        res = {n: f"resp-{n}" for n in ORDER}
        res["gemini"] = http.HttpError(429, "rpm", retry_after=30)
        r, book = self.make(res)
        r.complete("s", "p", NOW)
        self.assertEqual(r.complete("s", "p", NOW + timedelta(seconds=10))[1], "groq")
        res["gemini"] = "ok"
        self.assertEqual(r.complete("s", "p", NOW + timedelta(seconds=40))[1], "gemini")

    def test_missing_key_is_skipped(self):
        r, _ = self.make({n: f"resp-{n}" for n in ORDER}, env={"MISTRAL_KEY": "x"})
        self.assertEqual(r.complete("s", "p", NOW)[1], "mistral")

    def test_all_unavailable_raises_instead_of_charging(self):
        res = {n: http.HttpError(429, "quota") for n in ORDER}
        r, _ = self.make(res)
        with self.assertRaises(AllProvidersUnavailable):
            r.complete("s", "p", NOW)

    def test_local_daily_cap_stops_before_provider_limit(self):
        r, book = self.make({n: f"resp-{n}" for n in ORDER}, cap=2)
        for _ in range(2):
            self.assertEqual(r.complete("s", "p", NOW)[1], "gemini")
        self.assertEqual(r.complete("s", "p", NOW)[1], "groq")  # gemini bateu o teto local
        self.assertEqual(r.complete("s", "p", NOW + timedelta(days=1))[1], "gemini")  # novo dia

    def test_forbidden_pauses_24h(self):
        res = {n: "ok" for n in ORDER}
        res["gemini"] = http.HttpError(403, "plano")
        r, book = self.make(res)
        r.complete("s", "p", NOW)
        self.assertEqual(book.state("gemini").cooldown_until, (NOW + timedelta(hours=24)).isoformat())

    def test_bad_response_shape_counts_as_failure(self):
        res = {n: "ok" for n in ORDER}
        res["gemini"] = KeyError("choices")
        r, _ = self.make(res)
        self.assertEqual(r.complete("s", "p", NOW)[1], "groq")

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "q.json"
            book = QuotaBook(path=path)
            book.record_failure("gemini", 429, "x", None, NOW)
            book.save()
            again = QuotaBook.load(path)
            self.assertEqual(again.state("gemini").cooldown_until, book.state("gemini").cooldown_until)
            path.write_text("{lixo", encoding="utf-8")
            self.assertEqual(QuotaBook.load(path).providers, {})


if __name__ == "__main__":
    unittest.main()

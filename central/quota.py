"""Controle de cotas e revezamento. Estado em JSON (versionado no repositório pela Action).

Regras: cada provedor tem teto diário LOCAL (conservador, abaixo do limite gratuito) e "cooldown" quando o provedor
reclama (429/5xx/chave inválida). A cada pedido a ordem recomeça pela IA principal; o provedor em cooldown é pulado
até o prazo vencer, e assim a principal volta sozinha quando a cota renova.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class ProviderState:
    cooldown_until: str | None = None
    day: str = ""
    calls_today: int = 0
    last_error: str = ""
    failures: int = 0
    total_ok: int = 0
    total_fail: int = 0


@dataclass
class QuotaBook:
    path: Path | None = None
    providers: dict[str, ProviderState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "QuotaBook":
        book = cls(path=path)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for name, d in raw.get("providers", {}).items():
                    book.providers[name] = ProviderState(**{k: v for k, v in d.items() if k in ProviderState.__annotations__})
            except (ValueError, TypeError):
                book.providers = {}  # estado corrompido: recomeça (só perde o histórico de cota)
        return book

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"providers": {n: s.__dict__ for n, s in sorted(self.providers.items())}}
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def state(self, name: str) -> ProviderState:
        return self.providers.setdefault(name, ProviderState())

    def _roll_day(self, st: ProviderState, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        if st.day != today:
            st.day, st.calls_today = today, 0

    def available(self, name: str, daily_cap: int, now: datetime | None = None) -> tuple[bool, str]:
        now = now or utcnow()
        st = self.state(name)
        self._roll_day(st, now)
        if st.cooldown_until:
            until = datetime.fromisoformat(st.cooldown_until)
            if until > now:
                return False, f"em pausa até {st.cooldown_until}"
            st.cooldown_until = None  # renovou: volta a ser candidata
        if daily_cap and st.calls_today >= daily_cap:
            return False, f"teto diário local atingido ({daily_cap})"
        return True, ""

    def record_ok(self, name: str, now: datetime | None = None) -> None:
        now = now or utcnow()
        st = self.state(name)
        self._roll_day(st, now)
        st.calls_today += 1
        st.total_ok += 1
        st.failures = 0

    def record_failure(self, name: str, status: int, message: str, retry_after: float | None = None,
                       now: datetime | None = None) -> str:
        """Classifica o erro, define a pausa e devolve um texto para o registro."""
        now = now or utcnow()
        st = self.state(name)
        self._roll_day(st, now)
        st.calls_today += 1
        st.total_fail += 1
        st.failures += 1
        st.last_error = f"{status}: {message}"[:300]
        if status == 429:
            # Com Retry-After curto (limite por minuto) espera isso; sem ele, assume cota diária e espera a virada (UTC).
            if retry_after is not None and retry_after <= 6 * 3600:
                until = now + timedelta(seconds=max(retry_after, 1))
            else:
                until = next_utc_midnight(now)
            kind = "limite de cota"
        elif status in (401, 403):
            until = now + timedelta(hours=24)  # chave inválida ou modelo que exige plano pago: não insistir
            kind = "acesso negado (chave ou plano)"
        elif status == 404:
            until = now + timedelta(hours=24)
            kind = "modelo/endereço não encontrado"
        else:
            until = now + timedelta(minutes=15 * min(st.failures, 4))
            kind = "indisponível"
        st.cooldown_until = until.isoformat()
        return f"{kind}; pausa até {st.cooldown_until}"

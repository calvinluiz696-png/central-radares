"""Clientes das IAs gratuitas e o revezamento. Nenhuma chave fica no código: vêm de variáveis de ambiente
(GitHub Secrets). Os modelos e a ordem ficam em config/providers.json porque as condições gratuitas mudam."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import http
from .quota import QuotaBook, utcnow


class AllProvidersUnavailable(Exception):
    """Todas as IAs estão sem cota, sem chave ou fora do ar: o chamador pausa a etapa de IA."""


@dataclass
class ProviderConfig:
    name: str
    enabled: bool
    model: str
    endpoint: str
    env_key: str
    daily_cap: int
    extra_env: tuple[str, ...] = ()


def load_config(path: Path) -> tuple[list[str], dict[str, ProviderConfig]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cfgs = {}
    for name, d in raw["providers"].items():
        cfgs[name] = ProviderConfig(
            name=name, enabled=bool(d.get("enabled", True)), model=d["model"], endpoint=d["endpoint"],
            env_key=d["env_key"], daily_cap=int(d.get("daily_cap", 0)), extra_env=tuple(d.get("extra_env", [])),
        )
    return list(raw["order"]), cfgs


# --- Chamadas por provedor. Cada uma devolve só o TEXTO da resposta; erros viram http.HttpError. ---

def _openai_style(url: str, key: str, model: str, system: str, prompt: str) -> str:
    body = {
        "model": model, "temperature": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    r = http.post_json(url, body, {"Authorization": f"Bearer {key}"})
    return r["choices"][0]["message"]["content"]


def call_groq(cfg: ProviderConfig, key: str, system: str, prompt: str) -> str:
    return _openai_style(cfg.endpoint, key, cfg.model, system, prompt)


def call_mistral(cfg: ProviderConfig, key: str, system: str, prompt: str) -> str:
    return _openai_style(cfg.endpoint, key, cfg.model, system, prompt)


def call_gemini(cfg: ProviderConfig, key: str, system: str, prompt: str) -> str:
    url = cfg.endpoint.format(model=cfg.model)
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    r = http.post_json(url, body, {"x-goog-api-key": key})
    parts = r["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def call_cloudflare(cfg: ProviderConfig, key: str, system: str, prompt: str) -> str:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not account:
        raise http.HttpError(401, "CLOUDFLARE_ACCOUNT_ID ausente")
    url = cfg.endpoint.format(account_id=account, model=cfg.model)
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "temperature": 0}
    r = http.post_json(url, body, {"Authorization": f"Bearer {key}"})
    res = r.get("result", {})
    if isinstance(res, dict) and isinstance(res.get("response"), str):
        return res["response"]
    if isinstance(res, dict) and res.get("choices"):  # formato estilo OpenAI, usado por alguns modelos
        return res["choices"][0]["message"]["content"]
    raise http.HttpError(502, "resposta da Cloudflare em formato inesperado")


CALLERS: dict[str, Callable[[ProviderConfig, str, str, str], str]] = {
    "gemini": call_gemini, "groq": call_groq, "cloudflare": call_cloudflare, "mistral": call_mistral,
}


class Rotator:
    """Pede à IA principal; se estiver sem cota/chave/no ar, passa à próxima. Registra cada tentativa."""

    def __init__(self, order: list[str], configs: dict[str, ProviderConfig], book: QuotaBook,
                 callers: dict[str, Callable] | None = None, env: dict | None = None, log: Callable[[dict], None] | None = None):
        self.order, self.configs, self.book = order, configs, book
        self.callers = callers or CALLERS
        self.env = os.environ if env is None else env
        self.log = log or (lambda e: None)
        self.usage: dict[str, int] = {}

    def complete(self, system: str, prompt: str, now: datetime | None = None) -> tuple[str, str]:
        tried = []
        for name in self.order:
            cfg = self.configs.get(name)
            if cfg is None or not cfg.enabled:
                continue
            key = self.env.get(cfg.env_key, "")
            if not key:
                tried.append(f"{name}: sem chave ({cfg.env_key})")
                self.log({"provider": name, "event": "skip", "reason": "sem chave"})
                continue
            ok, why = self.book.available(name, cfg.daily_cap, now)
            if not ok:
                tried.append(f"{name}: {why}")
                self.log({"provider": name, "event": "skip", "reason": why})
                continue
            try:
                text = self.callers[name](cfg, key, system, prompt)
            except http.HttpError as e:
                note = self.book.record_failure(name, e.status, str(e), e.retry_after, now)
                tried.append(f"{name}: {note}")
                self.log({"provider": name, "event": "erro", "status": e.status, "detail": note})
                continue
            except (KeyError, IndexError, ValueError, TypeError) as e:
                note = self.book.record_failure(name, 502, f"resposta inesperada ({type(e).__name__})", None, now)
                tried.append(f"{name}: {note}")
                self.log({"provider": name, "event": "erro", "status": 502, "detail": note})
                continue
            self.book.record_ok(name, now)
            self.usage[name] = self.usage.get(name, 0) + 1
            self.log({"provider": name, "event": "ok"})
            return text, name
        raise AllProvidersUnavailable("; ".join(tried) or "nenhuma IA configurada")

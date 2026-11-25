"""HTTP mínimo (só biblioteca padrão). Funções separadas para os testes poderem trocá-las por falsas."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

USER_AGENT = "motorista-nav-central/1.0 (projeto pessoal sem fins lucrativos; dados abertos)"


class HttpError(Exception):
    def __init__(self, status: int, message: str = "", retry_after: float | None = None, body: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.retry_after = retry_after
        self.body = body


def _open(req: urllib.request.Request, timeout: float) -> tuple[int, dict, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URLs vêm da configuração)
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        ra = e.headers.get("Retry-After") if e.headers else None
        try:
            retry = float(ra) if ra is not None else None
        except ValueError:
            retry = None
        raise HttpError(e.code, str(e.reason), retry, e.read().decode("utf-8", "replace")[:500]) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HttpError(0, f"rede: {e}") from None


def get_text(url: str, timeout: float = 30, headers: dict | None = None) -> str:
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    _, _, body = _open(urllib.request.Request(url, headers=h), timeout)
    return body.decode("utf-8", "replace")


def post_json(url: str, body: dict, headers: dict | None = None, timeout: float = 90) -> dict:
    h = {"User-Agent": USER_AGENT, "Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode("utf-8")
    _, _, raw = _open(urllib.request.Request(url, data=data, headers=h, method="POST"), timeout)
    return json.loads(raw.decode("utf-8"))


def post_form(url: str, form: str, timeout: float = 120) -> str:
    h = {"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"}
    _, _, raw = _open(urllib.request.Request(url, data=form.encode("utf-8"), headers=h, method="POST"), timeout)
    return raw.decode("utf-8", "replace")

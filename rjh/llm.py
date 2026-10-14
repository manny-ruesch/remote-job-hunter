"""Abstraction LLM sans dépendance lourde (pas de LiteLLM) : ~250 lignes, stdlib uniquement.

    provider = create_provider(profile.llm)          # gemini | openai | ollama | anthropic | manual | ...
    resp = provider.complete(system, user)           # -> LLMResponse(text, usage, truncated…)
    provider.check()                                 # clé valide ? modèle encore disponible ? sinon suggestions

Principes :
  * la clé n'est lue que dans une VARIABLE D'ENVIRONNEMENT et n'apparaît jamais dans une URL ni un message
  * un modèle retiré (404) donne une erreur EXPLICITE avec la liste des modèles disponibles
  * le mode Manuel n'est pas un cas particulier du pipeline : c'est un fournisseur dont
    complete() lève ManualStepRequired(prompt) ; l'orchestrateur range alors le prompt dans une file
"""
from __future__ import annotations

import abc
import difflib
import os
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit

from .http import HttpClient, HttpError
from .jsonparse import JsonParseError, parse_json_loose


class LLMError(RuntimeError):
    pass


class LLMAuthError(LLMError):
    pass


class ModelUnavailableError(LLMError):
    pass


class ManualStepRequired(Exception):
    """Levée par ManualProvider : le prompt doit être collé par l'utilisateur dans le chat de son choix."""

    def __init__(self, system: str, user: str):
        super().__init__("étape manuelle requise")
        self.system, self.user = system, user

    @property
    def prompt(self) -> str:
        return f"{self.system}\n\n{self.user}"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    truncated: bool = False          # la sortie a atteint la limite de tokens


@dataclass
class LLMConfig:
    provider: str = "manual"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float | None = 0.2
    max_output_tokens: int = 4096
    timeout: float = 90.0
    json_mode: bool = True


@dataclass
class CheckResult:
    ok: bool
    message: str
    suggestions: list[str] = field(default_factory=list)


def _bare(model: str) -> str:
    return model.removeprefix("models/")


class LLMProvider(abc.ABC):
    name: str = ""
    interactive: bool = False       # True : nécessite une action humaine (mode Manuel)
    needs_key: bool = True

    def __init__(self, cfg: LLMConfig, http: HttpClient | None = None):
        self.cfg = cfg
        self.http = http or HttpClient(timeout=cfg.timeout, max_retries=2)

    @abc.abstractmethod
    def complete(self, system: str, user: str) -> LLMResponse: ...

    def list_models(self) -> list[str]:
        raise NotImplementedError(f"{self.name} ne sait pas lister ses modèles")

    # ------------------------------------------------------------------ diagnostic ---
    def check(self) -> CheckResult:
        if self.needs_key and not self.cfg.api_key:
            return CheckResult(False, f"{self.name} : aucune clé API (variable d'environnement non définie)")
        try:
            models = self.list_models()
        except NotImplementedError:
            return CheckResult(True, f"{self.name} : configuration plausible (pas de listing de modèles)")
        except HttpError as e:
            err = self._translate(e)
            return CheckResult(False, str(err))
        want = _bare(self.cfg.model)
        bare = [_bare(m) for m in models]
        if not want:
            return CheckResult(False, "aucun modèle configuré", bare[:8])
        if want in bare:
            return CheckResult(True, f"{self.name} : clé valide, modèle « {want} » disponible")
        close = difflib.get_close_matches(want, bare, n=5, cutoff=0.3) or bare[:5]
        return CheckResult(False, f"modèle « {want} » introuvable pour cette clé (retiré ou renommé ?)", close)

    # ------------------------------------------------------------------ utilitaires ---
    def _translate(self, e: HttpError) -> LLMError:
        body = e.body.lower()
        if e.status in (401, 403) or (e.status == 400 and ("api key" in body or "api_key" in body)):
            return LLMAuthError(f"{self.name} : clé refusée (HTTP {e.status})")
        if e.status == 404:
            return ModelUnavailableError(
                f"{self.name} : modèle « {self.cfg.model} » introuvable ou retiré (HTTP 404). "
                "Exécutez provider.check() pour voir les modèles disponibles avec votre clé."
            )
        # Autres erreurs : le statut seulement. Le corps de la réponse n'est jamais repris (un serveur peut y refléter une clé).
        return LLMError(f"{self.name} : erreur HTTP {e.status}" if e.status else f"{self.name} : connexion impossible")

    def complete_json(self, system: str, user: str, *, retries: int = 1) -> tuple[Any, LLMResponse]:
        last: Exception | None = None
        for _ in range(retries + 1):
            resp = self.complete(system, user)
            try:
                return parse_json_loose(resp.text), resp
            except JsonParseError as e:
                last = e
        raise LLMError(f"{self.name} : réponse non JSON après {retries + 1} essai(s)") from last


# ================================================================== Gemini =================

class GeminiProvider(LLMProvider):
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    @property
    def _base(self) -> str:
        return (self.cfg.base_url or self.BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.cfg.api_key}        # en-tête : la clé ne passe JAMAIS par l'URL

    def complete(self, system: str, user: str) -> LLMResponse:
        # Le préfixe « models/ » est obligatoire dans le chemin : on le normalise ici, une fois pour toutes.
        path = f"models/{_bare(self.cfg.model)}"
        gen: dict[str, Any] = {"maxOutputTokens": self.cfg.max_output_tokens}
        if self.cfg.temperature is not None:
            gen["temperature"] = self.cfg.temperature
        if self.cfg.json_mode:
            gen["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        try:
            data = self.http.post_json(f"{self._base}/{path}:generateContent", headers=self._headers(), json_body=body)
        except HttpError as e:
            raise self._translate(e) from e
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "réponse vide")
            raise LLMError(f"gemini : aucune réponse ({reason})")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        u = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            usage=Usage(u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)),
            model=self.cfg.model, provider=self.name,
            truncated=cands[0].get("finishReason") == "MAX_TOKENS",
        )

    def list_models(self) -> list[str]:
        data = self.http.get_json(f"{self._base}/models", params={"pageSize": 200}, headers=self._headers())
        return [m["name"] for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]


# ================================================================== OpenAI-compatible ======

# base_url, clé requise. (OpenAI et Ollama sont les cas de référence ; vérifiez les autres avant de vous y fier.)
OPENAI_COMPAT_PRESETS: dict[str, tuple[str, bool]] = {
    "openai": ("https://api.openai.com/v1", True),
    "ollama": ("http://localhost:11434/v1", False),
    "lmstudio": ("http://localhost:1234/v1", False),
    "openrouter": ("https://openrouter.ai/api/v1", True),
    "groq": ("https://api.groq.com/openai/v1", True),
    "mistral": ("https://api.mistral.ai/v1", True),
}


class OpenAICompatProvider(LLMProvider):
    """Tout serveur qui parle « /chat/completions » : OpenAI, Ollama, LM Studio, OpenRouter, Groq…"""
    name = "openai"

    def __init__(self, cfg: LLMConfig, http: HttpClient | None = None, *, needs_key: bool = True):
        super().__init__(cfg, http)
        self.needs_key = needs_key
        self.name = cfg.provider or "openai"

    @property
    def _base(self) -> str:
        return (self.cfg.base_url or OPENAI_COMPAT_PRESETS["openai"][0]).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}

    def _post(self, body: dict) -> Any:
        """Les serveurs « compatibles » divergent (response_format, temperature, max_tokens vs
        max_completion_tokens) : sur un 400 qui désigne le champ fautif, on le retire / on le renomme."""
        for _ in range(4):
            try:
                return self.http.post_json(f"{self._base}/chat/completions", headers=self._headers(), json_body=body)
            except HttpError as e:
                if e.status != 400:
                    raise self._translate(e) from e
                low, changed = e.body.lower(), False
                for key in ("response_format", "temperature"):
                    if key in low and key in body:
                        body.pop(key); changed = True
                if "max_tokens" in low and "max_tokens" in body:
                    body["max_completion_tokens"] = body.pop("max_tokens"); changed = True
                elif "max_completion_tokens" in low and "max_completion_tokens" in body:
                    body["max_tokens"] = body.pop("max_completion_tokens"); changed = True
                if not changed:
                    raise self._translate(e) from e
        raise LLMError(f"{self.name} : paramètres refusés par le serveur")

    def complete(self, system: str, user: str) -> LLMResponse:
        host = urlsplit(self._base).netloc
        tokens_key = "max_completion_tokens" if host.endswith("openai.com") else "max_tokens"
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tokens_key: self.cfg.max_output_tokens,
        }
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        if self.cfg.json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._post(body)
        choice = (data.get("choices") or [{}])[0]
        u = data.get("usage") or {}
        return LLMResponse(
            text=(choice.get("message") or {}).get("content") or "",
            usage=Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0)),
            model=self.cfg.model, provider=self.name,
            truncated=choice.get("finish_reason") == "length",
        )

    def list_models(self) -> list[str]:
        data = self.http.get_json(f"{self._base}/models", headers=self._headers())
        return [m["id"] for m in data.get("data", [])]


# ================================================================== Anthropic (clé API) ====

class AnthropicProvider(LLMProvider):
    """Clé API uniquement : les abonnements Claude grand public ne sont pas autorisés dans un outil tiers."""
    name = "anthropic"
    BASE = "https://api.anthropic.com/v1"

    @property
    def _base(self) -> str:
        return (self.cfg.base_url or self.BASE).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.cfg.api_key, "anthropic-version": "2023-06-01"}

    def complete(self, system: str, user: str) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.cfg.model, "max_tokens": self.cfg.max_output_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        try:
            data = self.http.post_json(f"{self._base}/messages", headers=self._headers(), json_body=body)
        except HttpError as e:
            raise self._translate(e) from e
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage") or {}
        return LLMResponse(
            text=text, usage=Usage(u.get("input_tokens", 0), u.get("output_tokens", 0)),
            model=self.cfg.model, provider=self.name, truncated=data.get("stop_reason") == "max_tokens",
        )

    def list_models(self) -> list[str]:
        data = self.http.get_json(f"{self._base}/models", params={"limit": 100}, headers=self._headers())
        return [m["id"] for m in data.get("data", [])]


# ================================================================== Manuel =================

class ManualProvider(LLMProvider):
    """Aucun appel réseau. complete() ne peut pas répondre : il lève ManualStepRequired(prompt),
    l'orchestrateur range le prompt dans une file ; l'utilisateur le colle dans ChatGPT / Claude /
    Gemini (avec son abonnement) puis recolle la réponse dans le dashboard."""
    name = "manual"
    interactive = True
    needs_key = False

    def complete(self, system: str, user: str) -> LLMResponse:
        raise ManualStepRequired(system, user)

    def check(self) -> CheckResult:
        return CheckResult(True, "mode manuel : aucune clé requise")


# ================================================================== Fabrique ===============

def create_provider(settings: Any, *, env: Mapping[str, str] | None = None, http: HttpClient | None = None) -> LLMProvider:
    """`settings` : profile.llm (LLMSettings) ou dict {provider, model, base_url, api_key_env, temperature}."""
    get = (lambda k, d="": settings.get(k, d)) if isinstance(settings, dict) else (lambda k, d="": getattr(settings, k, d))
    env = os.environ if env is None else env
    name = (get("provider") or "manual").lower()
    key_var = get("api_key_env")
    cfg = LLMConfig(
        provider=name, model=get("model"), base_url=get("base_url"),
        api_key=env.get(key_var, "") if key_var else "",
        temperature=get("temperature", 0.2),
    )
    if name == "manual":
        return ManualProvider(cfg, http)
    if name == "gemini":
        provider: LLMProvider = GeminiProvider(cfg, http)
    elif name == "anthropic":
        provider = AnthropicProvider(cfg, http)
    elif name in OPENAI_COMPAT_PRESETS or get("base_url"):
        # Serveur hors liste (vLLM, LocalAI…) : la clé n'est exigée que si l'utilisateur a indiqué api_key_env.
        base, needs_key = OPENAI_COMPAT_PRESETS.get(name, ("", bool(key_var)))
        cfg.base_url = cfg.base_url or base
        provider = OpenAICompatProvider(cfg, http, needs_key=needs_key)
    else:
        raise LLMError(
            f"fournisseur inconnu « {name} ». Valeurs : manual, gemini, anthropic, "
            f"{', '.join(OPENAI_COMPAT_PRESETS)} (ou n'importe quel serveur compatible via base_url)"
        )
    if provider.needs_key and not cfg.api_key:
        raise LLMAuthError(
            f"clé API absente pour « {name} » : définissez la variable d'environnement "
            f"{key_var or '(api_key_env non renseigné dans profile.yaml)'}"
        )
    return provider

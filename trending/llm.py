"""
Thin, provider-agnostic LLM wrapper with JSON helpers and a prompt cache.

    llm = get_llm()                 # None when USE_LLM=False or no key/fixture
    data = llm.json(system, user)   # -> dict (or None if the model failed)

Providers
* "anthropic" (default) - Claude via the Anthropic SDK. Model from LLM_MODEL,
  default claude-sonnet-5-5. Current Claude models do not accept a sampling
  temperature, so determinism comes from low effort + strict JSON instructions
  + the prompt cache below.
* "openai" - via the OpenAI SDK, temperature 0 and JSON mode.

To add another provider, implement `_call_<name>(system, user, max_tokens) -> str`.

Every response is cached under fixtures/<namespace>/llm/ keyed by a hash of the
prompt, so re-runs are instant and free, and REPLAY works with no API key.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from . import cache
from . import config as cfg

log = logging.getLogger("trending")


class LLM:
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self._client: Any = None
        self.calls = 0       # live calls made this session
        self.cache_hits = 0
        self.seconds = 0.0   # total wall time spent in live calls

    def __repr__(self) -> str:
        return f"LLM(provider={self.provider!r}, model={self.model!r})"

    # ------------------------------------------------------------------ #
    def json(self, system: str, user: str, max_tokens: int = 8000) -> dict[str, Any] | None:
        """Ask for a JSON object. Returns the parsed dict, or None on failure."""
        key = cache.key_of({"system": system, "user": user})
        hit = cache.get("llm", key, ttl=None)
        if hit is not None:
            self.cache_hits += 1
            return hit
        if cfg.REPLAY:
            log.warning("REPLAY: no LLM fixture for this prompt; falling back")
            return None
        for attempt in range(2):
            try:
                t0 = time.time()
                text = getattr(self, f"_call_{self.provider}")(system, user, max_tokens)
                self.seconds += time.time() - t0
                self.calls += 1
                data = parse_json(text)
                if data is not None:
                    cache.put("llm", key, data, request={"system": system[:200], "model": self.model})
                    return data
                log.warning("LLM returned non-JSON (attempt %d)", attempt + 1)
            except Exception as exc:  # noqa: BLE001 - never break the pipeline on LLM errors
                log.warning("LLM call failed (attempt %d): %s", attempt + 1, exc)
        return None

    # ------------------------------------------------------------------ #
    def _call_anthropic(self, system: str, user: str, max_tokens: int) -> str:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=3)
        resp = self._client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": "low"},
            # If a request is declined by a safety classifier, retry on a
            # fallback model inside the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model declined the request")
        return "".join(b.text for b in resp.content if b.type == "text")

    def _call_openai(self, system: str, user: str, max_tokens: int) -> str:
        import openai

        if self._client is None:
            self._client = openai.OpenAI(max_retries=3)
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""


def parse_json(text: str) -> dict[str, Any] | None:
    """Parse a JSON object, tolerating ```json fences or leading prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                val = json.loads(m.group(0))
                return val if isinstance(val, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def get_llm() -> LLM | None:
    """
    The configured LLM, or None (=> lexical / heuristic fallbacks are used).

    Provider: LLM_PROVIDER env/config, else Anthropic if ANTHROPIC_API_KEY is
    set, else OpenAI if OPENAI_API_KEY is set. In REPLAY mode no key is needed:
    answers come from the saved fixtures.
    """
    if not cfg.USE_LLM:
        return None
    provider = (cfg.LLM_PROVIDER or "").lower()
    if not provider:
        if os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        elif cfg.REPLAY:
            provider = "anthropic"  # replay serves cached answers; provider unused
        else:
            print("No LLM key found (ANTHROPIC_API_KEY / OPENAI_API_KEY) → using lexical fallbacks.")
            return None
    if provider not in cfg.DEFAULT_MODELS:
        raise ValueError(f"Unknown LLM_PROVIDER {provider!r}; use one of {list(cfg.DEFAULT_MODELS)}")
    return LLM(provider, cfg.LLM_MODEL or cfg.DEFAULT_MODELS[provider])

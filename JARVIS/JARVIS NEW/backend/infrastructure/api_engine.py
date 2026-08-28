"""
backend/infrastructure/api_engine.py
Unified Multi-Provider API Engine for J.A.R.V.I.S.
Manages client initialization, concurrency-safe dynamic credential rotation, 
and intelligent status-classified failover across LLM, Vision, STT, and TTS providers.
"""

import logging
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional
import httpx

from backend.infrastructure.config import settings
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Infrastructure.APIEngine")


class APIEngine:
    """
    Unified Multi-Provider API Engine for J.A.R.V.I.S.
    Manages client initialization, concurrency-safe dynamic credential rotation,
    and intelligent status-classified failover across LLM, Vision, STT, and TTS providers.
    """
    def __init__(self):
        self._rotation_pointers: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        
        # Config-driven or default cascading priorities
        self.DEFAULT_PROVIDER_CASCADE = ["gemini", "groq", "openrouter", "cerebras", "mistral", "cloudflare"]

        # Shared process-lifetime HTTP client (connection pooling; closed via aclose()).
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Returns the shared pooled AsyncClient, creating it lazily after shutdown/close."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    @asynccontextmanager
    async def _pooled_client(self):
        """
        Async-context helper yielding the SHARED pooled client without closing it.
        Keeps every provider call site compatible (`async with self._pooled_client() as client:`)
        while reusing connections across requests instead of building a new AsyncClient per call.
        Per-call timeouts collapse to the shared client default (60s), which safely covers
        the previous 45s LLM/vision budgets.
        """
        yield await self._get_http_client()

    async def aclose(self):
        """Gracefully closes the shared HTTP client (call during application shutdown)."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _get_provider_keys_with_rotation(self, provider: str) -> List[str]:
        """Returns provider keys ordered with current rotation pointer at front using concurrency-safe locking."""
        async with self._lock:
            keys = settings.get_provider_keys(provider)
            if not keys:
                return []
            
            current_idx = self._rotation_pointers.get(provider, 0)
            rotated_keys = keys[current_idx % len(keys):] + keys[:current_idx % len(keys)]
            self._rotation_pointers[provider] = (current_idx + 1) % len(keys)
            return rotated_keys

    async def _get_cloudflare_accounts_with_rotation(self) -> List[Dict[str, str]]:
        """Returns Cloudflare accounts ordered with rotation pointer at front using concurrency-safe locking."""
        async with self._lock:
            accounts = settings.get_cloudflare_accounts()
            if not accounts:
                return []
            
            current_idx = self._rotation_pointers.get("cloudflare", 0)
            rotated_accounts = accounts[current_idx % len(accounts):] + accounts[:current_idx % len(accounts)]
            self._rotation_pointers["cloudflare"] = (current_idx + 1) % len(accounts)
            return rotated_accounts

    def _classify_http_error(self, status_code: int) -> ErrorClassification:
        """
        Maps HTTP status codes to strict Step 7.5 ErrorClassifications
        to support deterministic retry decisions in TaskQueue.
        """
        if status_code in {401, 403}:
            return ErrorClassification.AUTHORIZATION_FAILURE
        if status_code in {400, 404, 422}:
            return ErrorClassification.VALIDATION_FAILURE
        if status_code == 429 or (500 <= status_code < 600):
            return ErrorClassification.TRANSIENT_PROVIDER
        return ErrorClassification.UNKNOWN_ERROR

    def _sanitize_error_text(self, status_code: int, provider: str) -> str:
        """Sanitizes upstream error text to prevent API token or provider payload leakage."""
        return f"Provider [{provider}] returned HTTP status {status_code}"

    # ==========================================
    # LLM SUBSYSTEM (Multi-Provider Cascade)
    # ==========================================

    async def call_llm(
        self, 
        prompt: str, 
        provider: Optional[str] = None, 
        model: Optional[str] = None, 
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """
        Dispatches prompt to specified provider or cascades through available providers
        until a successful generation is returned, returning a unified normalized response.
        """
        providers_to_try = [provider.lower()] if provider else list(settings.llm_provider_cascade or self.DEFAULT_PROVIDER_CASCADE)
        if not provider:
            # Only attempt providers that actually have credentials configured, so the
            # cascade instantly skips empty/unconfigured attempts (e.g. no gemini key
            # should never waste a full request cycle before reaching a working provider).
            # Cloudflare's credential presence is checked internally, so keep it eligible.
            providers_to_try = [
                p for p in providers_to_try
                if p == "cloudflare" or settings.get_provider_keys(p)
            ]
        last_error_msg = "No providers configured or attempted."
        last_classification = ErrorClassification.UNKNOWN_ERROR

        if not providers_to_try:
            raise ExecutionError(
                message="No LLM providers have API keys configured for the active cascade.",
                classification=ErrorClassification.AUTHORIZATION_FAILURE,
            )

        for current_provider in providers_to_try:
            logger.info(f"APIEngine dispatching LLM request to provider: [{current_provider}] (Model: {model or 'provider-default'})")
            
            start_time = time.time()
            if current_provider in ["groq", "openrouter", "cerebras", "mistral"]:
                res = await self._call_openai_compatible_with_failover(current_provider, prompt, model, system_prompt, max_tokens, temperature)
            elif current_provider == "gemini":
                res = await self._call_gemini_with_failover(prompt, model, system_prompt, max_tokens, temperature)
            elif current_provider == "cloudflare":
                res = await self._call_cloudflare_with_failover(prompt, model, system_prompt, max_tokens, temperature)
            else:
                continue

            latency_ms = round((time.time() - start_time) * 1000, 2)

            if res.get("success") and str(res.get("response") or "").strip():
                res["latency_ms"] = latency_ms
                return res
            
            last_error_msg = res.get("error") or "Provider returned an empty response body."
            last_classification = res.get("classification") or ErrorClassification.UNKNOWN_ERROR
            logger.warning(f"Provider [{current_provider}] failed or returned empty content: {last_error_msg}. Escalating...")

        # Cascade exhausted: raise canonical ExecutionError for TaskQueue retry routing
        raise ExecutionError(
            message=f"All configured LLM providers failed. Last error: {last_error_msg}",
            classification=last_classification
        )

    async def _call_openai_compatible_with_failover(
        self, provider: str, prompt: str, model: Optional[str], system_prompt: Optional[str], max_tokens: int, temperature: float
    ) -> Dict[str, Any]:
        keys = await self._get_provider_keys_with_rotation(provider)
        if not keys:
            return {
                "success": False, 
                "error": f"No API keys configured for [{provider}]", 
                "classification": ErrorClassification.AUTHORIZATION_FAILURE,
                "response": None,
                "provider": provider,
                "model": model,
                "usage": {},
                "latency_ms": 0.0
            }

        default_models = {
            "groq": getattr(settings, "GROQ_DEFAULT_MODEL", None) or "qwen/qwen3.6-27b",
            "openrouter": getattr(settings, "OPENROUTER_DEFAULT_MODEL", None) or "dots-studio/dots-3-note-preview:free",
            "cerebras": getattr(settings, "CEREBRAS_DEFAULT_MODEL", None) or "llama3.1-8b",
            "mistral": getattr(settings, "MISTRAL_DEFAULT_MODEL", None) or "mistral-medium-2508"
        }
        target_model = model or default_models.get(provider, "gpt-3.5-turbo")

        base_urls = {
            "groq": "https://api.groq.com/openai/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "cerebras": "https://api.cerebras.ai/v1",
            "mistral": "https://api.mistral.ai/v1"
        }
        base_url = base_urls.get(provider)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": target_model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}

        last_error = None
        last_classification = ErrorClassification.UNKNOWN_ERROR
        for api_key in keys:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            try:
                async with self._pooled_client() as client:
                    response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                    if response.status_code == 200:
                        data = response.json()
                        choices = data.get("choices") or []
                        message = (choices[0].get("message") or {}) if choices else {}
                        content = message.get("content")
                        if not (content and str(content).strip()):
                            # Reasoning-first models (notably OpenRouter :free
                            # reasoning variants) may return the visible answer
                            # in `reasoning` with an empty `content` field.
                            content = message.get("reasoning")
                        content = str(content).strip() if content else ""
                        usage = data.get("usage", {})
                        return {
                            "success": True, 
                            "error": None, 
                            "classification": None,
                            "response": content, 
                            "provider": provider, 
                            "model": target_model,
                            "usage": usage,
                            "latency_ms": 0.0
                        }
                    
                    last_error = self._sanitize_error_text(response.status_code, provider)
                    last_classification = self._classify_http_error(response.status_code)
                    if response.status_code in [400, 401, 403, 404, 422]:
                        break
            except httpx.TimeoutException:
                last_error = f"Provider [{provider}] request timed out."
                last_classification = ErrorClassification.TIMEOUT
            except Exception as e:
                last_error = f"Provider [{provider}] network connection failure: {str(e)}"
                last_classification = ErrorClassification.NETWORK_FAILURE

        return {
            "success": False, 
            "error": last_error, 
            "classification": last_classification, 
            "response": None,
            "provider": provider,
            "model": target_model,
            "usage": {},
            "latency_ms": 0.0
        }

    async def _call_gemini_with_failover(
        self, prompt: str, model: Optional[str], system_prompt: Optional[str], max_tokens: int, temperature: float
    ) -> Dict[str, Any]:
        keys = await self._get_provider_keys_with_rotation("gemini")
        if not keys:
            return {
                "success": False, 
                "error": "No API keys configured for [gemini]", 
                "classification": ErrorClassification.AUTHORIZATION_FAILURE,
                "response": None,
                "provider": "gemini",
                "model": model,
                "usage": {},
                "latency_ms": 0.0
            }

        target_model = model or (getattr(settings, "GEMINI_DEFAULT_MODEL", None) or "gemma-4-31b-it")
        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"System Instruction: {system_prompt}"}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}

        last_error = None
        last_classification = ErrorClassification.UNKNOWN_ERROR
        for api_key in keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"
            # API key is transmitted via the x-goog-api-key header, never the URL
            # query string (avoids credential leakage into logs/history/proxies).
            headers = {"x-goog-api-key": api_key}
            try:
                async with self._pooled_client() as client:
                    response = await client.post(url, json=payload, headers=headers)
                    if response.status_code == 200:
                        data = response.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            content = candidates[0]["content"]["parts"][0]["text"]
                            usage = data.get("usageMetadata", {})
                            return {
                                "success": True, 
                                "error": None, 
                                "classification": None,
                                "response": content, 
                                "provider": "gemini", 
                                "model": target_model,
                                "usage": usage,
                                "latency_ms": 0.0
                            }
                        last_error = "Gemini returned empty candidate list."
                        last_classification = ErrorClassification.VALIDATION_FAILURE
                    else:
                        last_error = self._sanitize_error_text(response.status_code, "gemini")
                        last_classification = self._classify_http_error(response.status_code)
                        if response.status_code in [400, 401, 403, 404, 422]:
                            break
            except httpx.TimeoutException:
                last_error = "Gemini API request timed out."
                last_classification = ErrorClassification.TIMEOUT
            except Exception as e:
                last_error = f"Gemini connection failure: {str(e)}"
                last_classification = ErrorClassification.NETWORK_FAILURE

        return {
            "success": False, 
            "error": last_error, 
            "classification": last_classification, 
            "response": None,
            "provider": "gemini",
            "model": target_model,
            "usage": {},
            "latency_ms": 0.0
        }

    async def _call_cloudflare_with_failover(
        self, prompt: str, model: Optional[str], system_prompt: Optional[str], max_tokens: int, temperature: float
    ) -> Dict[str, Any]:
        accounts = await self._get_cloudflare_accounts_with_rotation()
        if not accounts:
            return {
                "success": False, 
                "error": "No Cloudflare credentials configured", 
                "classification": ErrorClassification.AUTHORIZATION_FAILURE,
                "response": None,
                "provider": "cloudflare",
                "model": model,
                "usage": {},
                "latency_ms": 0.0
            }

        target_model = model or (getattr(settings, "CLOUDFLARE_DEFAULT_MODEL", None) or "@cf/meta/llama-3-8b-instruct")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}

        last_error = None
        last_classification = ErrorClassification.UNKNOWN_ERROR
        for account in accounts:
            api_key, account_id = account.get("api_key"), account.get("account_id")
            if not api_key or not account_id:
                continue

            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{target_model}"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

            try:
                async with self._pooled_client() as client:
                    response = await client.post(url, headers=headers, json=payload)
                    if response.status_code == 200:
                        data = response.json()
                        result = data.get("result", {})
                        content = result.get("response") or result.get("text")
                        if content:
                            return {
                                "success": True, 
                                "error": None, 
                                "classification": None,
                                "response": content, 
                                "provider": "cloudflare", 
                                "model": target_model,
                                "usage": {},
                                "latency_ms": 0.0
                            }
                        last_error = "Cloudflare returned empty generation payload."
                        last_classification = ErrorClassification.VALIDATION_FAILURE
                    else:
                        last_error = self._sanitize_error_text(response.status_code, "cloudflare")
                        last_classification = self._classify_http_error(response.status_code)
                        if response.status_code in [400, 401, 403, 404, 422]:
                            break
            except httpx.TimeoutException:
                last_error = "Cloudflare request timed out."
                last_classification = ErrorClassification.TIMEOUT
            except Exception as e:
                last_error = f"Cloudflare connection failure: {str(e)}"
                last_classification = ErrorClassification.NETWORK_FAILURE

        return {
            "success": False, 
            "error": last_error, 
            "classification": last_classification, 
            "response": None,
            "provider": "cloudflare",
            "model": target_model,
            "usage": {},
            "latency_ms": 0.0
        }

    # ==========================================
    # VISION SUBSYSTEM (Normalized Response)
    # ==========================================

    async def analyze_vision(self, image_base64: str, query: str) -> Dict[str, Any]:
        """
        Config-driven multimodal vision processing with intelligent provider failover
        returning a unified normalized response contract.
        """
        cascade = getattr(settings, "VISION_PROVIDER_CASCADE", ["openai", "openrouter", "gemini"])
        last_error = "No vision providers configured or attempted."
        last_classification = ErrorClassification.UNKNOWN_ERROR

        start_time = time.time()
        for provider in cascade:
            keys = await self._get_provider_keys_with_rotation(provider)
            if not keys:
                continue

            for api_key in keys:
                try:
                    if provider == "openai":
                        model = getattr(settings, "OPENAI_VISION_MODEL", "gpt-4o")
                        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                        payload = {
                            "model": model,
                            "messages": [{"role": "user", "content": [{"type": "text", "text": query}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}]}],
                            "max_tokens": 800
                        }
                        async with self._pooled_client() as client:
                            resp = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                            if resp.status_code == 200:
                                data = resp.json()
                                return {
                                    "success": True,
                                    "error": None,
                                    "classification": None,
                                    "response": data["choices"][0]["message"]["content"],
                                    "provider": "openai",
                                    "model": model,
                                    "usage": data.get("usage", {}),
                                    "latency_ms": round((time.time() - start_time) * 1000, 2)
                                }
                            last_error = self._sanitize_error_text(resp.status_code, "openai")
                            last_classification = self._classify_http_error(resp.status_code)

                    elif provider == "openrouter":
                        model = getattr(settings, "OPENROUTER_VISION_MODEL", "dots-studio/dots-3-note-preview:free")
                        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                        payload = {
                            "model": model,
                            "messages": [{"role": "user", "content": [{"type": "text", "text": query}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}]}],
                            "max_tokens": 800
                        }
                        async with self._pooled_client() as client:
                            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
                            if resp.status_code == 200:
                                data = resp.json()
                                return {
                                    "success": True,
                                    "error": None,
                                    "classification": None,
                                    "response": data["choices"][0]["message"]["content"],
                                    "provider": "openrouter",
                                    "model": model,
                                    "usage": data.get("usage", {}),
                                    "latency_ms": round((time.time() - start_time) * 1000, 2)
                                }
                            last_error = self._sanitize_error_text(resp.status_code, "openrouter")
                            last_classification = self._classify_http_error(resp.status_code)

                    elif provider == "gemini":
                        model = getattr(settings, "GEMINI_VISION_MODEL", "gemma-4-31b-it")
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                        payload = {"contents": [{"parts": [{"text": query}, {"inline_data": {"mime_type": "image/jpeg", "data": image_base64}}]}]}
                        gemini_headers = {"x-goog-api-key": api_key}
                        async with self._pooled_client() as client:
                            resp = await client.post(url, json=payload, headers=gemini_headers)
                            if resp.status_code == 200:
                                data = resp.json()
                                candidates = data.get("candidates", [])
                                if candidates:
                                    return {
                                        "success": True,
                                        "error": None,
                                        "classification": None,
                                        "response": candidates[0]["content"]["parts"][0]["text"],
                                        "provider": "gemini",
                                        "model": model,
                                        "usage": data.get("usageMetadata", {}),
                                        "latency_ms": round((time.time() - start_time) * 1000, 2)
                                    }
                            last_error = self._sanitize_error_text(resp.status_code, "gemini")
                            last_classification = self._classify_http_error(resp.status_code)
                except httpx.TimeoutException:
                    last_error = f"Vision provider [{provider}] timed out."
                    last_classification = ErrorClassification.TIMEOUT
                except Exception as e:
                    last_error = f"Vision provider [{provider}] network error: {str(e)}"
                    last_classification = ErrorClassification.NETWORK_FAILURE

        raise ExecutionError(
            message=f"All vision providers failed. Last error: {last_error}",
            classification=last_classification
        )

    # ==========================================
    # SPEECH-TO-TEXT (STT) SUBSYSTEM
    # ==========================================

    async def transcribe_audio(self, audio_bytes: bytes, language: str = "en") -> Dict[str, Any]:
        """
        Config-driven transcription with fallback across Groq, OpenAI, etc.
        returning a unified normalized response contract.
        """
        cascade = getattr(settings, "STT_PROVIDER_CASCADE", ["groq", "openai"])
        last_error = "No STT providers configured or attempted."
        last_classification = ErrorClassification.UNKNOWN_ERROR

        start_time = time.time()
        for provider in cascade:
            keys = await self._get_provider_keys_with_rotation(provider)
            if not keys:
                continue

            base_url = "https://api.groq.com/openai/v1" if provider == "groq" else "https://api.openai.com/v1"
            model = getattr(settings, f"{provider.upper()}_STT_MODEL", "whisper-large-v3-turbo" if provider == "groq" else "whisper-1")

            for api_key in keys:
                try:
                    headers = {"Authorization": f"Bearer {api_key}"}
                    files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
                    data = {"model": model, "language": language}

                    async with self._pooled_client() as client:
                        resp = await client.post(f"{base_url}/audio/transcriptions", headers=headers, files=files, data=data)
                        if resp.status_code == 200:
                            res_json = resp.json()
                            transcript_text = res_json.get("text", "")
                            return {
                                "success": True,
                                "error": None,
                                "classification": None,
                                # Canonical transcript field consumed by SpeechToTextTool
                                # and AudioStreamPublisher ("text"), with "response"
                                # retained for legacy normalized-contract callers.
                                "text": transcript_text,
                                "response": transcript_text,
                                "provider": provider,
                                "model": model,
                                "usage": {},
                                "latency_ms": round((time.time() - start_time) * 1000, 2)
                            }
                        last_error = self._sanitize_error_text(resp.status_code, provider)
                        last_classification = self._classify_http_error(resp.status_code)
                except httpx.TimeoutException:
                    last_error = f"STT provider [{provider}] timed out."
                    last_classification = ErrorClassification.TIMEOUT
                except Exception as e:
                    last_error = f"STT provider [{provider}] network error: {str(e)}"
                    last_classification = ErrorClassification.NETWORK_FAILURE

        raise ExecutionError(
            message=f"All transcription providers failed. Last error: {last_error}",
            classification=last_classification
        )

    # ==========================================
    # TEXT-TO-SPEECH (TTS) SUBSYSTEM (Stream-to-Bytes)
    # ==========================================

    async def generate_tts(self, text: str, voice: str) -> Dict[str, Any]:
        """
        Config-driven Text-to-Speech synthesis with multi-provider failover
        returning raw audio bytes and normalized metadata without filesystem bypass.
        """
        cascade = getattr(settings, "TTS_PROVIDER_CASCADE", ["openai", "elevenlabs"])
        last_error = "No TTS providers configured or attempted."
        last_classification = ErrorClassification.UNKNOWN_ERROR

        start_time = time.time()
        for provider in cascade:
            keys = await self._get_provider_keys_with_rotation(provider)
            if not keys:
                if provider == "local" and getattr(settings, "LOCAL_TTS_BASE_URL", None):
                    keys = ["local-dummy-key"]
                else:
                    continue

            model = getattr(settings, f"{provider.upper()}_TTS_MODEL", "tts-1")

            for api_key in keys:
                try:
                    if provider in ["openai", "local"]:
                        base_url = getattr(settings, "LOCAL_TTS_BASE_URL", "https://api.openai.com/v1") if provider == "local" else "https://api.openai.com/v1"
                        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                        payload = {"model": model, "input": text, "voice": voice}

                        async with self._pooled_client() as client:
                            async with client.stream("POST", f"{base_url}/audio/speech", headers=headers, json=payload) as resp:
                                if resp.status_code == 200:
                                    audio_bytes = await resp.aread()
                                    return {
                                        "success": True,
                                        "error": None,
                                        "classification": None,
                                        "response_bytes": audio_bytes,
                                        "provider": provider,
                                        "model": model,
                                        "usage": {},
                                        "latency_ms": round((time.time() - start_time) * 1000, 2)
                                    }
                                last_error = self._sanitize_error_text(resp.status_code, provider)
                                last_classification = self._classify_http_error(resp.status_code)

                    elif provider == "elevenlabs":
                        voice_id = getattr(settings, "ELEVENLABS_DEFAULT_VOICE_ID", voice)
                        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
                        headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
                        payload = {"text": text, "model_id": model}

                        async with self._pooled_client() as client:
                            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                                if resp.status_code == 200:
                                    audio_bytes = await resp.aread()
                                    return {
                                        "success": True,
                                        "error": None,
                                        "classification": None,
                                        "response_bytes": audio_bytes,
                                        "provider": "elevenlabs",
                                        "model": model,
                                        "usage": {},
                                        "latency_ms": round((time.time() - start_time) * 1000, 2)
                                    }
                                last_error = self._sanitize_error_text(resp.status_code, "elevenlabs")
                                last_classification = self._classify_http_error(resp.status_code)
                except httpx.TimeoutException:
                    last_error = f"TTS provider [{provider}] timed out."
                    last_classification = ErrorClassification.TIMEOUT
                except Exception as e:
                    last_error = f"TTS provider [{provider}] network error: {str(e)}"
                    last_classification = ErrorClassification.NETWORK_FAILURE

        raise ExecutionError(
            message=f"TTS generation failed across all providers. Last error: {last_error}",
            classification=last_classification
        )


api_engine = APIEngine()
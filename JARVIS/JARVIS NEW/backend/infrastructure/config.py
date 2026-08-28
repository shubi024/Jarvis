import os
from typing import Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # --- Application Settings ---
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    PROJECT_NAME: str = "JARVIS"
    VERSION: str = "1.0.0"
    # Loopback-only by default: this is a personal single-operator system, so the
    # API must never silently bind to every interface. Set HOST explicitly (e.g.
    # HOST=0.0.0.0) only when a deliberate remote deployment is intended — and
    # then JARVIS_AUTH_TOKEN becomes mandatory for any consequential route.
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # --- Transport Authentication ---
    # When set, REST/WS clients must present this as `Authorization: Bearer <token>`
    # (REST) or `?token=<value>` (WebSocket). When unset, only loopback connections
    # are accepted (the interactive local operator remains trusted by design).
    JARVIS_AUTH_TOKEN: Optional[str] = None

    # --- Filesystem Hard Boundary (LOCKED Security Architecture §6) ---
    # Allowed roots are: the user's Downloads folder and Local Disk E:.
    # Everything else is DENIED by default. No L4 grant can override this boundary.
    # FILESYSTEM_EXTRA_ALLOWED_PATHS may add further explicit user-authorized roots
    # (comma-separated); it exists so future expansion requires deliberate authorization.
    FILESYSTEM_EXTRA_ALLOWED_PATHS: str = ""

    # --- Database & Infrastructure ---
    DATABASE_URL: str
    REDIS_URL: str

    # --- LLM Provider: Groq Keys ---
    GROQ_API_KEY_1: Optional[str] = None
    GROQ_API_KEY_2: Optional[str] = None
    GROQ_API_KEY_3: Optional[str] = None
    GROQ_API_KEY_4: Optional[str] = None
    GROQ_API_KEY_5: Optional[str] = None

    # --- LLM Provider: Gemini Keys ---
    GEMINI_API_KEY_1: Optional[str] = None
    GEMINI_API_KEY_2: Optional[str] = None
    GEMINI_API_KEY_3: Optional[str] = None
    GEMINI_API_KEY_4: Optional[str] = None
    GEMINI_API_KEY_5: Optional[str] = None

    # --- LLM Provider: OpenRouter Keys ---
    OPENROUTER_API_KEY_1: Optional[str] = None
    OPENROUTER_API_KEY_2: Optional[str] = None
    OPENROUTER_API_KEY_3: Optional[str] = None
    OPENROUTER_API_KEY_4: Optional[str] = None
    OPENROUTER_API_KEY_5: Optional[str] = None

    # --- LLM Provider: Cerebras Keys ---
    CEREBRAS_API_KEY_1: Optional[str] = None
    CEREBRAS_API_KEY_2: Optional[str] = None
    CEREBRAS_API_KEY_3: Optional[str] = None
    CEREBRAS_API_KEY_4: Optional[str] = None
    CEREBRAS_API_KEY_5: Optional[str] = None

    # --- LLM Provider: Mistral Keys ---
    MISTRAL_API_KEY_1: Optional[str] = None
    MISTRAL_API_KEY_2: Optional[str] = None
    MISTRAL_API_KEY_3: Optional[str] = None
    MISTRAL_API_KEY_4: Optional[str] = None
    MISTRAL_API_KEY_5: Optional[str] = None

    # --- TTS Provider: OpenAI Keys (also used by cloud STT fallback) ---
    OPENAI_API_KEY_1: Optional[str] = None
    OPENAI_API_KEY_2: Optional[str] = None
    OPENAI_API_KEY_3: Optional[str] = None
    OPENAI_API_KEY_4: Optional[str] = None
    OPENAI_API_KEY_5: Optional[str] = None

    # --- TTS Provider: ElevenLabs Keys ---
    ELEVENLABS_API_KEY_1: Optional[str] = None
    ELEVENLABS_API_KEY_2: Optional[str] = None
    ELEVENLABS_API_KEY_3: Optional[str] = None
    ELEVENLABS_API_KEY_4: Optional[str] = None
    ELEVENLABS_API_KEY_5: Optional[str] = None

    # --- Voice Provider Options ---
    LOCAL_TTS_BASE_URL: Optional[str] = None          # OpenAI-compatible local TTS server
    ELEVENLABS_DEFAULT_VOICE_ID: Optional[str] = None

    # --- LLM Provider: Cloudflare Multi-Account Credentials ---
    CLOUDFLARE_API_KEY_1: Optional[str] = None
    CLOUDFLARE_ACCOUNT_ID_1: Optional[str] = None
    CLOUDFLARE_USER_EMAIL_1: Optional[str] = None

    CLOUDFLARE_API_KEY_2: Optional[str] = None
    CLOUDFLARE_ACCOUNT_ID_2: Optional[str] = None
    CLOUDFLARE_USER_EMAIL_2: Optional[str] = None

    CLOUDFLARE_API_KEY_3: Optional[str] = None
    CLOUDFLARE_ACCOUNT_ID_3: Optional[str] = None
    CLOUDFLARE_USER_EMAIL_3: Optional[str] = None

    CLOUDFLARE_API_KEY_4: Optional[str] = None
    CLOUDFLARE_ACCOUNT_ID_4: Optional[str] = None
    CLOUDFLARE_USER_EMAIL_4: Optional[str] = None

    # --- Tool Configuration: Cloudflare DNS Manager ---
    CLOUDFLARE_API_TOKEN: Optional[str] = None

    # --- LLM Provider Cascade & Default Models ---
    # Optional comma-separated provider precedence, e.g. "openrouter,groq,gemini".
    # When empty, APIEngine falls back to its internal DEFAULT_PROVIDER_CASCADE.
    # These fields MUST be declared here because Settings uses extra="ignore",
    # so undeclared variables in .env would otherwise be silently dropped.
    LLM_PROVIDER_CASCADE: Optional[str] = None
    GEMINI_DEFAULT_MODEL: Optional[str] = None
    GROQ_DEFAULT_MODEL: Optional[str] = None
    OPENROUTER_DEFAULT_MODEL: Optional[str] = None
    CEREBRAS_DEFAULT_MODEL: Optional[str] = None
    MISTRAL_DEFAULT_MODEL: Optional[str] = None
    CLOUDFLARE_DEFAULT_MODEL: Optional[str] = None

    # --- Tool Configuration: SMTP Email ---
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_USE_SSL: bool = False

    # --- Tool Configuration: Twilio Messaging ---
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None

    # --- Tool Configuration: Google Workspace ---
    GOOGLE_TOKEN_PATH: str = "token.json"

    # --- Tool Configuration: Meta Ads ---
    META_APP_ID: Optional[str] = None
    META_APP_SECRET: Optional[str] = None
    META_ACCESS_TOKEN: Optional[str] = None
    META_AD_ACCOUNT_ID: Optional[str] = None

    # --- Tool Configuration: Google Ads ---
    GOOGLE_ADS_DEVELOPER_TOKEN: Optional[str] = None
    GOOGLE_ADS_CLIENT_ID: Optional[str] = None
    GOOGLE_ADS_CLIENT_SECRET: Optional[str] = None
    GOOGLE_ADS_REFRESH_TOKEN: Optional[str] = None
    GOOGLE_ADS_LOGIN_CUSTOMER_ID: Optional[str] = None
    GOOGLE_ADS_CUSTOMER_ID: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("DEBUG", mode="before")
    @classmethod
    def normalize_debug_value(cls, value):
        """Accept legacy deployment labels without ever enabling debug implicitly."""
        if isinstance(value, str) and value.strip().lower() in {"release", "production", "prod"}:
            return False
        return value

    @property
    def llm_provider_cascade(self) -> list[str]:
        """Parses the optional comma-separated LLM_PROVIDER_CASCADE into an ordered list.

        Empty string / whitespace returns an empty list so APIEngine falls back to its
        hard-coded default cascade. Confidence usernames resolve to clean lowercase ids.
        """
        raw = (self.LLM_PROVIDER_CASCADE or "").strip()
        if not raw:
            return []
        return [p.strip().lower() for p in raw.split(",") if p.strip()]

    @property
    def llm_keys(self) -> dict[str, list[str]]:
        """Aggregates configured provider API keys by provider for diagnostics and runtime rotation.
        Includes the voice providers (OpenAI, ElevenLabs) so APIEngine's TTS/STT
        cascades can obtain credentials through the same get_provider_keys() path."""
        providers = {
            "groq": [self.GROQ_API_KEY_1, self.GROQ_API_KEY_2, self.GROQ_API_KEY_3, self.GROQ_API_KEY_4, self.GROQ_API_KEY_5],
            "gemini": [self.GEMINI_API_KEY_1, self.GEMINI_API_KEY_2, self.GEMINI_API_KEY_3, self.GEMINI_API_KEY_4, self.GEMINI_API_KEY_5],
            "openrouter": [self.OPENROUTER_API_KEY_1, self.OPENROUTER_API_KEY_2, self.OPENROUTER_API_KEY_3, self.OPENROUTER_API_KEY_4, self.OPENROUTER_API_KEY_5],
            "cerebras": [self.CEREBRAS_API_KEY_1, self.CEREBRAS_API_KEY_2, self.CEREBRAS_API_KEY_3, self.CEREBRAS_API_KEY_4, self.CEREBRAS_API_KEY_5],
            "mistral": [self.MISTRAL_API_KEY_1, self.MISTRAL_API_KEY_2, self.MISTRAL_API_KEY_3, self.MISTRAL_API_KEY_4, self.MISTRAL_API_KEY_5],
            "openai": [self.OPENAI_API_KEY_1, self.OPENAI_API_KEY_2, self.OPENAI_API_KEY_3, self.OPENAI_API_KEY_4, self.OPENAI_API_KEY_5],
            "elevenlabs": [self.ELEVENLABS_API_KEY_1, self.ELEVENLABS_API_KEY_2, self.ELEVENLABS_API_KEY_3, self.ELEVENLABS_API_KEY_4, self.ELEVENLABS_API_KEY_5],
        }
        return {
            provider: [k for k in keys if k] 
            for provider, keys in providers.items() 
            if any(keys)
        }

    @property
    def allowed_filesystem_roots(self) -> list[str]:
        """
        Resolves the LOCKED filesystem allowlist:
          1. The user's Downloads folder (always permitted).
          2. Local Disk E:\\ (always permitted per locked spec).
          3. Any explicitly authorized extra roots from FILESYSTEM_EXTRA_ALLOWED_PATHS.
        Legacy compatibility: if JARVIS_WORKSPACE is set in the environment, it is
        included as an extra root so existing deployments keep functioning.
        """
        import os
        from pathlib import Path

        roots: list[str] = []

        downloads = Path.home() / "Downloads"
        roots.append(str(downloads))

        e_drive = Path("E:/")
        roots.append(str(e_drive))

        legacy_workspace = os.getenv("JARVIS_WORKSPACE")
        if legacy_workspace:
            roots.append(legacy_workspace)

        for raw in self.FILESYSTEM_EXTRA_ALLOWED_PATHS.split(","):
            candidate = raw.strip()
            if candidate:
                roots.append(candidate)

        return roots

    @property
    def cloudflare_accounts(self) -> list[dict[str, Optional[str]]]:
        """Aggregates configured Cloudflare multi-account credentials."""
        accounts = []
        for i in range(1, 5):
            api_key = getattr(self, f"CLOUDFLARE_API_KEY_{i}", None)
            account_id = getattr(self, f"CLOUDFLARE_ACCOUNT_ID_{i}", None)
            email = getattr(self, f"CLOUDFLARE_USER_EMAIL_{i}", None)
            if api_key or account_id or email:
                accounts.append({
                    "api_key": api_key,
                    "account_id": account_id,
                    "email": email
                })
        return accounts

    # --- ADD THESE TWO NEW METHODS HERE ---
    def get_provider_keys(self, provider: str) -> list[str]:
        """Helper method for APIEngine to fetch keys by provider name."""
        return self.llm_keys.get(provider.lower(), [])

    def get_cloudflare_accounts(self) -> list[dict[str, Optional[str]]]:
        """Helper method for APIEngine to fetch Cloudflare accounts."""
        return self.cloudflare_accounts
    # --------------------------------------

# Initialize and export settings globally
settings = Settings()

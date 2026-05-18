"""OAuth client framework — Sprint 6c · Phase 4.3 facade.

Pi parity: ``packages/ai/src/utils/oauth/`` (SHA 734e08e).

Public surface:

- :class:`OAuthCredentials` / :class:`OAuthLoginCallbacks` /
  :class:`OAuthProvider` Protocol — :mod:`.types`.
- :func:`generate_pkce` — :mod:`._pkce`.
- :func:`start_callback_server` — :mod:`._callback_server`.
- :func:`login_anthropic` / :func:`refresh_anthropic_token` /
  :data:`ANTHROPIC_OAUTH_PROVIDER` — :mod:`.anthropic`.
- :func:`get_oauth_provider` / :func:`register_oauth_provider` and
  friends — :mod:`._registry`.
- :class:`AuthStorage` / :func:`default_auth_path` — :mod:`.auth_storage`.
- :func:`get_oauth_api_key_from_credentials` — :mod:`._high_level`.
"""

from __future__ import annotations

from aelix_ai.oauth._high_level import (
    OAuthRefreshResult,
    get_oauth_api_key_from_credentials,
)
from aelix_ai.oauth._registry import (
    _OAUTH_DEFERRED_PROVIDERS,
    _PHASE_4_DEFERRED_FEATURES,
    get_oauth_provider,
    get_oauth_providers,
    register_oauth_provider,
    reset_oauth_providers,
    unregister_oauth_provider,
)
from aelix_ai.oauth.anthropic import (
    ANTHROPIC_OAUTH_ID,
    ANTHROPIC_OAUTH_PROVIDER,
    AUTHORIZE_URL,
    CALLBACK_HOST,
    CALLBACK_PATH,
    CALLBACK_PORT,
    CLIENT_ID,
    REDIRECT_URI,
    SCOPES,
    TOKEN_URL,
    login_anthropic,
    refresh_anthropic_token,
)
from aelix_ai.oauth.auth_storage import AuthStorage, default_auth_path
from aelix_ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthProvider,
    OAuthSelectOption,
    OAuthSelectPrompt,
)

__all__ = [
    "ANTHROPIC_OAUTH_ID",
    "ANTHROPIC_OAUTH_PROVIDER",
    "AUTHORIZE_URL",
    "AuthStorage",
    "CALLBACK_HOST",
    "CALLBACK_PATH",
    "CALLBACK_PORT",
    "CLIENT_ID",
    "OAuthAuthInfo",
    "OAuthCredentials",
    "OAuthLoginCallbacks",
    "OAuthPrompt",
    "OAuthProvider",
    "OAuthRefreshResult",
    "OAuthSelectOption",
    "OAuthSelectPrompt",
    "REDIRECT_URI",
    "SCOPES",
    "TOKEN_URL",
    "_OAUTH_DEFERRED_PROVIDERS",
    "_PHASE_4_DEFERRED_FEATURES",
    "default_auth_path",
    "get_oauth_api_key_from_credentials",
    "get_oauth_provider",
    "get_oauth_providers",
    "login_anthropic",
    "refresh_anthropic_token",
    "register_oauth_provider",
    "reset_oauth_providers",
    "unregister_oauth_provider",
]

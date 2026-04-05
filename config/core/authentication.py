import uuid

import jwt
from jwt import PyJWKClient
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

# Fetches and caches Supabase's public keys (ES256).
# Falls back to HS256 with SUPABASE_JWT_SECRET if SUPABASE_URL is not set.
_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        supabase_url = settings.SUPABASE_URL
        if supabase_url:
            jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
            _jwks_client = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
    return _jwks_client


CLINIC_ROLES   = frozenset({'admin', 'doctor', 'lab_tech', 'receptionist'})
REQUIRED_ROLES = CLINIC_ROLES | frozenset({'super_admin'})


class JWTUser:
    """
    Lightweight user object built entirely from validated JWT claims.
    No database query is made during authentication — clinic_id and user_role
    are trusted from the token, which is signed by Supabase and injected at
    mint time via the custom_access_token_hook.

    Super admin users have role='super_admin' and clinic_id=None.
    All clinic-scoped users have a non-null clinic_id.
    """
    is_authenticated = True
    is_anonymous = False

    def __init__(self, user_id: uuid.UUID, clinic_id, role: str):
        self.id = user_id
        self.clinic_id = clinic_id   # None for super_admin
        self.role = role

    def __str__(self):
        return f"JWTUser(id={self.id}, clinic={self.clinic_id}, role={self.role})"


class SupabaseJWTAuthentication(BaseAuthentication):
    """
    Validates Supabase-issued JWTs and enforces required claims.

    Required claims in the token payload:
      sub        — user UUID (standard JWT subject)
      clinic_id  — injected by custom_access_token_hook
      user_role  — injected by custom_access_token_hook

    Supports:
      ES256 — asymmetric, validated via Supabase JWKS endpoint
      HS256 — symmetric, validated via SUPABASE_JWT_SECRET

    Sets request.user to a JWTUser instance. No DB query is performed.
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:].strip()
        payload = self._decode_token(token)

        user_id = self._require_claim(payload, "sub")
        role    = self._require_claim(payload, "user_role")

        if role not in REQUIRED_ROLES:
            raise AuthenticationFailed(
                f"Invalid user_role '{role}'. Must be one of: {', '.join(sorted(REQUIRED_ROLES))}."
            )

        try:
            parsed_user_id = uuid.UUID(user_id)
        except (ValueError, AttributeError):
            raise AuthenticationFailed("Token contains malformed UUID in 'sub'.")

        # Super admin tokens have no clinic_id — that is valid and intentional.
        parsed_clinic_id = None
        if role != 'super_admin':
            clinic_id = self._require_claim(payload, "clinic_id")
            try:
                parsed_clinic_id = uuid.UUID(clinic_id)
            except (ValueError, AttributeError):
                raise AuthenticationFailed("Token contains malformed UUID in 'clinic_id'.")

        return (JWTUser(parsed_user_id, parsed_clinic_id, role), token)

    def authenticate_header(self, request):
        return "Bearer"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _decode_token(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
            alg = header.get("alg", "HS256")

            if alg == "ES256":
                client = _get_jwks_client()
                if client is None:
                    raise AuthenticationFailed(
                        "JWKS client unavailable — SUPABASE_URL is not configured."
                    )
                signing_key = client.get_signing_key_from_jwt(token)
                return jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["ES256"],
                    audience="authenticated",
                )
            else:
                return jwt.decode(
                    token,
                    settings.SUPABASE_JWT_SECRET,
                    algorithms=["HS256"],
                    audience="authenticated",
                )

        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Token expired.")
        except jwt.InvalidAudienceError:
            raise AuthenticationFailed("Invalid token audience.")
        except jwt.InvalidTokenError as exc:
            raise AuthenticationFailed(f"Invalid token: {exc}")

    @staticmethod
    def _require_claim(payload: dict, claim: str) -> str:
        value = payload.get(claim)
        if not value:
            raise AuthenticationFailed(
                f"Token is missing required claim: '{claim}'. "
                "Ensure the Supabase custom_access_token_hook is enabled."
            )
        return value

"""
Router autenticazione con 2FA (TOTP + passkey WebAuthn).

Flusso login:
  POST /api/login            {password}            -> {pending: true, token_pending, methods:[...]}  oppure {token} se 2FA non configurato
  POST /api/2fa/totp/verify  {code}  (Bearer pending) -> {token}
  POST /api/webauthn/login/begin    (Bearer pending) -> options
  POST /api/webauthn/login/complete (Bearer pending) -> {token}

Setup (richiede token pieno):
  GET  /api/2fa/totp/setup           -> {secret, otpauth_uri, qr_svg}
  POST /api/2fa/totp/enable {code}   -> attiva TOTP
  POST /api/2fa/totp/disable
  POST /api/webauthn/register/begin  -> options
  POST /api/webauthn/register/complete {credential} -> ok
  GET  /api/2fa/status               -> {totp_enabled, passkeys:[...]}
"""
import base64
import hashlib
import io
import json
import secrets

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..rate_limit import limiter
from ..auth import (
    make_token, make_pending_token, make_image_token, require_auth, require_pending,
    verify_password, hash_password, get_or_bootstrap_admin,
    get_passkeys, set_passkeys, has_2fa, verify_totp_and_consume, bearer,
)

router = APIRouter(prefix="/api", tags=["auth"])

# Challenge WebAuthn condivise via Redis (NON in memoria: con piu' worker gunicorn
# la challenge generata da un worker non sarebbe vista dall'altro al completamento).
import redis.asyncio as aioredis  # noqa: E402

_redis_client = None


def _redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    return _redis_client


async def _challenge_set(key: str, value: bytes, ttl: int = 300):
    await _redis().set(f"webauthn:{key}", value, ex=ttl)


async def _challenge_get(key: str) -> bytes | None:
    return await _redis().get(f"webauthn:{key}")


async def _challenge_del(key: str):
    await _redis().delete(f"webauthn:{key}")


def _sid(cred: HTTPAuthorizationCredentials | None) -> str:
    """
    Suffisso di sessione derivato dal bearer corrente.

    begin e complete usano lo STESSO token (pending per il login, full per la
    registrazione), quindi ricadono sulla stessa challenge. Due login concorrenti
    (device/tab diversi) hanno token diversi -> chiavi Redis diverse -> non si
    sovrascrivono piu' la challenge a vicenda (prima erano chiavi globali condivise).
    """
    raw = (cred.credentials if cred else "") or ""
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ============ LOGIN (fase 1: username + password) ============
class LoginIn(BaseModel):
    username: str = ""
    password: str


@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, payload: LoginIn, s: AsyncSession = Depends(get_session)):
    admin = await get_or_bootstrap_admin(s)
    # username case-insensitive; se il client non lo manda (vecchia UI) si controlla solo la password
    if payload.username and payload.username.strip().lower() != (admin.username or "").lower():
        raise HTTPException(401, "Credenziali errate")
    if not verify_password(payload.password, admin.password_hash):
        raise HTTPException(401, "Credenziali errate")

    # se non ha 2FA configurato -> entra subito, ma segnala di configurarlo
    if not has_2fa(admin):
        return {"token": make_token(), "image_token": make_image_token(), "needs_2fa_setup": True}

    # 2FA attivo: rilascia token pending e lista metodi disponibili
    methods = []
    if admin.totp_enabled:
        methods.append("totp")
    if get_passkeys(admin):
        methods.append("passkey")
    return {"pending": True, "token_pending": make_pending_token(), "methods": methods}


# ============ TOTP ============
class TotpCode(BaseModel):
    code: str


@router.get("/2fa/totp/setup")
async def totp_setup(_: bool = Depends(require_auth), s: AsyncSession = Depends(get_session)):
    """Genera (o rigenera, se non ancora attivo) il secret TOTP + QR da scansionare."""
    admin = await get_or_bootstrap_admin(s)
    if admin.totp_enabled:
        raise HTTPException(400, "TOTP gia' attivo. Disattivalo prima di rigenerarlo.")
    secret = pyotp.random_base32()
    admin.totp_secret = secret
    await s.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=admin.username, issuer_name=settings.TOTP_ISSUER)
    # QR come SVG inline
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    qr_svg = buf.getvalue().decode("utf-8")
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": qr_svg}


@router.post("/2fa/totp/enable")
async def totp_enable(payload: TotpCode, _: bool = Depends(require_auth), s: AsyncSession = Depends(get_session)):
    admin = await get_or_bootstrap_admin(s)
    if not admin.totp_secret:
        raise HTTPException(400, "Nessun secret TOTP generato. Fai prima il setup.")
    if not pyotp.TOTP(admin.totp_secret).verify(payload.code.strip(), valid_window=1):
        raise HTTPException(400, "Codice non valido")
    admin.totp_enabled = True
    await s.commit()
    return {"ok": True}


@router.post("/2fa/totp/disable")
async def totp_disable(_: bool = Depends(require_auth), s: AsyncSession = Depends(get_session)):
    admin = await get_or_bootstrap_admin(s)
    admin.totp_enabled = False
    admin.totp_secret = ""
    await s.commit()
    return {"ok": True}


@router.post("/2fa/totp/verify")
@limiter.limit("10/minute")
async def totp_verify(request: Request, payload: TotpCode, _: dict = Depends(require_pending), s: AsyncSession = Depends(get_session)):
    """Secondo fattore in fase di login: verifica il codice e rilascia il token pieno.

    Usa verify_totp_and_consume che blocca il replay dello stesso codice nella
    sua finestra di validita' (vedi auth.py).
    """
    admin = await get_or_bootstrap_admin(s)
    if not admin.totp_enabled or not admin.totp_secret:
        raise HTTPException(400, "TOTP non attivo")
    if not verify_totp_and_consume(admin, payload.code):
        raise HTTPException(401, "Codice non valido")
    await s.commit()
    return {"token": make_token(), "image_token": make_image_token()}


@router.get("/image-token")
async def image_token(_: bool = Depends(require_auth)):
    """
    Rilascia un nuovo token scoped per le immagini (scope='image', TTL 10 min).
    Richiede un JWT pieno valido come Bearer header (refresh trasparente lato frontend).
    """
    return {"image_token": make_image_token()}


# ============ stato 2FA ============
@router.get("/2fa/status")
async def twofa_status(_: bool = Depends(require_auth), s: AsyncSession = Depends(get_session)):
    admin = await get_or_bootstrap_admin(s)
    pks = get_passkeys(admin)
    return {
        "username": admin.username,
        "totp_enabled": admin.totp_enabled,
        "passkeys": [{"label": p.get("label", "passkey"), "id": p.get("id", "")} for p in pks],
    }


# ============ cambio username ============
class ChangeUsername(BaseModel):
    username: str


@router.post("/account/username")
async def change_username(payload: ChangeUsername, _: bool = Depends(require_auth), s: AsyncSession = Depends(get_session)):
    u = payload.username.strip()
    if len(u) < 2:
        raise HTTPException(400, "Username troppo corto")
    admin = await get_or_bootstrap_admin(s)
    admin.username = u
    await s.commit()
    return {"ok": True, "username": u}


# ============ cambio password ============
class ChangePw(BaseModel):
    old_password: str
    new_password: str


@router.post("/account/password")
async def change_password(payload: ChangePw, _: bool = Depends(require_auth), s: AsyncSession = Depends(get_session)):
    admin = await get_or_bootstrap_admin(s)
    if not verify_password(payload.old_password, admin.password_hash):
        raise HTTPException(401, "Password attuale errata")
    if len(payload.new_password) < 8:
        raise HTTPException(400, "La nuova password deve avere almeno 8 caratteri")
    admin.password_hash = hash_password(payload.new_password)
    await s.commit()
    return {"ok": True}


# ============ PASSKEY / WebAuthn ============
# Richiede WEBAUTHN_RP_ID e WEBAUTHN_ORIGIN configurati nel .env.
from webauthn import (  # noqa: E402
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (  # noqa: E402
    PublicKeyCredentialDescriptor, UserVerificationRequirement,
    AuthenticatorSelectionCriteria, ResidentKeyRequirement,
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _check_webauthn_configured():
    if not settings.WEBAUTHN_RP_ID or not settings.WEBAUTHN_ORIGIN:
        raise HTTPException(400, "Passkey non configurate: imposta WEBAUTHN_RP_ID e WEBAUTHN_ORIGIN nel .env")


class PasskeyLabel(BaseModel):
    label: str = "passkey"


@router.post("/webauthn/register/begin")
async def webauthn_register_begin(_: bool = Depends(require_auth), cred: HTTPAuthorizationCredentials | None = Depends(bearer), s: AsyncSession = Depends(get_session)):
    _check_webauthn_configured()
    admin = await get_or_bootstrap_admin(s)
    existing = get_passkeys(admin)
    opts = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=str(admin.id).encode(),
        user_name=admin.username,
        user_display_name=admin.username,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=_unb64(p["id"])) for p in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    await _challenge_set(f"register:{_sid(cred)}", opts.challenge)
    return json.loads(options_to_json(opts))


class RegisterComplete(BaseModel):
    credential: dict
    label: str = "passkey"


@router.post("/webauthn/register/complete")
async def webauthn_register_complete(payload: RegisterComplete, _: bool = Depends(require_auth), cred: HTTPAuthorizationCredentials | None = Depends(bearer), s: AsyncSession = Depends(get_session)):
    _check_webauthn_configured()
    ckey = f"register:{_sid(cred)}"
    challenge = await _challenge_get(ckey)
    if not challenge:
        raise HTTPException(400, "Nessuna challenge attiva")
    try:
        verification = verify_registration_response(
            credential=payload.credential,
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Registrazione passkey fallita: {e}")

    admin = await get_or_bootstrap_admin(s)
    pks = get_passkeys(admin)
    pks.append({
        "id": _b64(verification.credential_id),
        "public_key": _b64(verification.credential_public_key),
        "sign_count": verification.sign_count,
        "label": payload.label or "passkey",
    })
    set_passkeys(admin, pks)
    await s.commit()
    await _challenge_del(ckey)
    return {"ok": True}


@router.post("/webauthn/register/delete")
async def webauthn_delete(payload: PasskeyLabel, _: bool = Depends(require_auth), s: AsyncSession = Depends(get_session)):
    admin = await get_or_bootstrap_admin(s)
    pks = [p for p in get_passkeys(admin) if p.get("id") != payload.label]
    set_passkeys(admin, pks)
    await s.commit()
    return {"ok": True}


@router.post("/webauthn/login/begin")
async def webauthn_login_begin(_: dict = Depends(require_pending), cred: HTTPAuthorizationCredentials | None = Depends(bearer), s: AsyncSession = Depends(get_session)):
    _check_webauthn_configured()
    admin = await get_or_bootstrap_admin(s)
    pks = get_passkeys(admin)
    if not pks:
        raise HTTPException(400, "Nessuna passkey registrata")
    opts = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        allow_credentials=[PublicKeyCredentialDescriptor(id=_unb64(p["id"])) for p in pks],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    await _challenge_set(f"login:{_sid(cred)}", opts.challenge)
    return json.loads(options_to_json(opts))


class LoginComplete(BaseModel):
    credential: dict


@router.post("/webauthn/login/complete")
@limiter.limit("10/minute")
async def webauthn_login_complete(request: Request, payload: LoginComplete, _: dict = Depends(require_pending), cred: HTTPAuthorizationCredentials | None = Depends(bearer), s: AsyncSession = Depends(get_session)):
    _check_webauthn_configured()
    ckey = f"login:{_sid(cred)}"
    challenge = await _challenge_get(ckey)
    if not challenge:
        raise HTTPException(400, "Nessuna challenge attiva")
    admin = await get_or_bootstrap_admin(s)
    pks = get_passkeys(admin)
    # trova la passkey usata
    cred_id = payload.credential.get("id") or payload.credential.get("rawId", "")
    match = None
    for p in pks:
        if p["id"] == cred_id or p["id"] == cred_id.rstrip("="):
            match = p
            break
    if not match:
        raise HTTPException(401, "Passkey sconosciuta")
    try:
        verification = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=_unb64(match["public_key"]),
            credential_current_sign_count=match.get("sign_count", 0),
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(401, f"Verifica passkey fallita: {e}")

    # aggiorna sign_count (anti-replay)
    match["sign_count"] = verification.new_sign_count
    set_passkeys(admin, pks)
    await s.commit()
    await _challenge_del(ckey)
    return {"token": make_token(), "image_token": make_image_token()}

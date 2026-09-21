"""
Autenticazione admin (single-user) con 2FA.
Flusso: password -> token "pending" -> secondo fattore (TOTP o passkey) -> token pieno.
La password vive come hash bcrypt nel DB (bootstrap dal .env al primo avvio).
"""
import json
from datetime import datetime, timedelta, timezone

import bcrypt
import pyotp
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import SessionLocal
from .models import AdminAuth

bearer = HTTPBearer(auto_error=False)


# ---------- password (bcrypt diretto: passlib e' incompatibile con bcrypt 4.x) ----------
def hash_password(plain: str) -> str:
    # bcrypt ha un limite di 72 byte: tronco in modo sicuro
    pw = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("ascii"))
    except Exception:  # noqa: BLE001
        return False


# ---------- admin record ----------
async def get_or_bootstrap_admin(s: AsyncSession) -> AdminAuth:
    """
    Ritorna l'admin (id=1). Se non esiste, lo crea usando ADMIN_PASSWORD dal .env
    come password iniziale (hashata). Da quel momento la password vive solo nel DB.
    """
    admin = (await s.execute(select(AdminAuth).limit(1))).scalar_one_or_none()
    if admin is None:
        admin = AdminAuth(
            username="admin",
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            passkeys="[]",
        )
        s.add(admin)
        await s.commit()
        await s.refresh(admin)
    return admin


def get_passkeys(admin: AdminAuth) -> list:
    try:
        return json.loads(admin.passkeys or "[]")
    except Exception:  # noqa: BLE001
        return []


def set_passkeys(admin: AdminAuth, items: list) -> None:
    admin.passkeys = json.dumps(items)


def has_2fa(admin: AdminAuth) -> bool:
    return bool(admin.totp_enabled) or bool(get_passkeys(admin))


# ---------- TOTP anti-replay ----------
def verify_totp_and_consume(admin: AdminAuth, code: str) -> bool:
    """
    Verifica un codice TOTP E rifiuta il replay nella stessa finestra.

    Senza questa difesa, lo stesso codice di 30s e' riutilizzabile per ~90s (con
    valid_window=1). Se intercettato (XSS, MITM, shoulder surfing), un attaccante
    avrebbe una finestra concreta per riusarlo. Qui memorizziamo l'ultima window
    consumata e blocchiamo qualsiasi codice di window <= a quella.

    Implementazione: usiamo pyotp.TOTP.verify() (che gestisce gia' correttamente il
    drift +/-30s) per validare; poi, se valido, determiniamo a quale window appartiene
    il codice e blocchiamo il replay confrontandola con admin.totp_last_window.

    NOTA: chi chiama deve fare `await s.commit()` dopo, per persistere la window.
    """
    if not admin.totp_secret:
        return False
    code = (code or "").strip()
    if not code:
        return False

    totp = pyotp.TOTP(admin.totp_secret)
    # 1) validazione standard (drift +/-30s)
    if not totp.verify(code, valid_window=1):
        return False

    # 2) calcola la window a cui appartiene effettivamente il codice ricevuto.
    # pyotp.TOTP.timecode(datetime) -> intero della window (incrementa di 1 ogni 30s).
    # Provo le 3 window valide (ora-30s, ora, ora+30s) e prendo quella il cui codice
    # generato uguaglia il codice ricevuto.
    now_dt = datetime.now(timezone.utc)
    matched_window = None
    for offset in (-1, 0, 1):
        t = now_dt + timedelta(seconds=offset * 30)
        if totp.at(t) == code:
            matched_window = totp.timecode(t)
            break

    # difesa estrema: se per qualche motivo non trovo la window (non dovrebbe mai
    # succedere visto che verify() e' passato), accetto comunque ma senza tracciare
    # la window (non blocca, non avanza)
    if matched_window is None:
        return True

    # 3) anti-replay
    last = admin.totp_last_window or 0
    if matched_window <= last:
        return False
    admin.totp_last_window = matched_window
    return True


# ---------- token ----------
def make_token() -> str:
    """Token di sessione PIENO (rilasciato solo dopo il 2FA completato)."""
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_HOURS)
    return jwt.encode({"sub": "admin", "scope": "full", "exp": exp},
                      settings.JWT_SECRET, algorithm="HS256")


def make_pending_token() -> str:
    """Token temporaneo tra password e 2FA: vale 5 minuti, non da' accesso ai dati."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=5)
    return jwt.encode({"sub": "admin", "scope": "pending", "exp": exp},
                      settings.JWT_SECRET, algorithm="HS256")


def make_image_token() -> str:
    """
    Token scoped SOLO per /api/sites/{id}/image, vita breve (10 minuti).

    Le immagini degli screenshot vengono caricate via <img src> e il browser non puo'
    inviare l'header Authorization, quindi il token finisce in query string. La query
    string viene loggata ovunque (nginx, NPMplus, browser history, header Referer):
    usare il JWT pieno (12h, scope=full) in query string sarebbe un rischio concreto
    di leak. Qui usiamo un token separato con scope='image' che vale solo per le
    immagini e dura poco: anche se viene letto da un log, non da' accesso alle API.
    """
    exp = datetime.now(timezone.utc) + timedelta(minutes=10)
    return jwt.encode({"sub": "admin", "scope": "image", "exp": exp},
                      settings.JWT_SECRET, algorithm="HS256")


def _decode(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        return None


def require_auth(cred: HTTPAuthorizationCredentials | None = Depends(bearer)):
    """Richiede un token PIENO (2FA completato)."""
    if cred is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    data = _decode(cred.credentials)
    if not data or data.get("scope") != "full":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return True


def require_pending(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    """Richiede il token 'pending' (fase 2FA). Usato dagli endpoint del secondo fattore."""
    if cred is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    data = _decode(cred.credentials)
    if not data or data.get("scope") not in ("pending", "full"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return data


def verify_image_token(token: str) -> bool:
    """
    Verifica il token scoped per le immagini (scope='image' o 'full' per retrocompat).
    Usato come query param da media.py, non come Bearer header.
    """
    if not token:
        return False
    data = _decode(token)
    if not data:
        return False
    return data.get("scope") in ("image", "full")


def verify_token(token: str) -> bool:
    """
    DEPRECATA: alias retrocompatibile di verify_image_token.
    Mantenuta perche' indicizzata altrove come 'verify_token'.
    """
    return verify_image_token(token)
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from slowapi.errors import RateLimitExceeded
from .db import engine, run_migrations
from . import models  # noqa: F401  (registra i modelli)
from .rate_limit import limiter
from .routers import sites, auth, media, install, security, connectors, agent, history, notifications, expiries, branding, preferences, reports, stats
from .auth import require_auth
from .version import __version__, APP_NAME, VENDOR, VENDOR_URL, AUTHOR


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Con gunicorn -w 2 il lifespan gira in ENTRAMBI i worker al boot, in parallelo.
    # run_migrations prende un advisory lock di transazione, quindi i processi si
    # serializzano: il primo crea/migra, gli altri rieseguono gli ALTER idempotenti.
    async with engine.begin() as conn:
        await run_migrations(conn)
    yield


app = FastAPI(title="Panopticon Lite", lifespan=lifespan)

# registra il limiter sull'app + handler per 429
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    # messaggio italiano e generico (non sveliamo dettagli sul limite)
    return JSONResponse(
        status_code=429,
        content={"detail": "Troppi tentativi. Riprova tra qualche minuto."},
    )


app.include_router(auth.router)
app.include_router(sites.router)
app.include_router(media.router)
app.include_router(install.router)
app.include_router(security.router)   # Centro Sicurezza
app.include_router(connectors.router)  # archivio connettori Joomla/WP
app.include_router(agent.router)       # auto-registrazione siti dal connettore
app.include_router(history.router)     # storico update 7gg (Sentinel)
app.include_router(notifications.router)  # template notifiche editabili
app.include_router(reports.router)       # report mensile PDF
app.include_router(stats.router)         # statistiche dashboard
app.include_router(stats.router_public)  # PDF statistiche (token in query)
app.include_router(expiries.router)       # scadenze domini/licenze
app.include_router(branding.router)       # logo/favicon configurabili
app.include_router(preferences.router)      # impostazioni operative UI (no segreti)


@app.get("/api/version", dependencies=[Depends(require_auth)])
async def app_version():
    """Versione e crediti mostrati nel pie' di pagina del pannello."""
    return {"version": __version__, "name": APP_NAME, "vendor": VENDOR,
            "vendor_url": VENDOR_URL, "author": AUTHOR}


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse("static/sentinel/index.html")


@app.get("/favicon.ico")
async def favicon():
    return RedirectResponse("/api/branding/favicon")


app.mount("/static", StaticFiles(directory="static"), name="static")

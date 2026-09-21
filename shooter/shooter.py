"""
Shooter: servizio interno che cattura l'anteprima di un sito.

Contratto (usato dal worker):
    POST /shot  {"url": "...", "site_id": 12}  ->  {"path": "site_12.jpg"}

Il file viene scritto in /data/screenshots (volume condiviso in sola lettura con l'api,
che lo serve su /api/sites/{id}/image). Nessuna autenticazione: il servizio non e'
esposto all'esterno, vive solo sulla rete interna di compose.
"""
import asyncio
import logging
import os
import re

from fastapi import FastAPI, HTTPException, Body
from playwright.async_api import async_playwright

log = logging.getLogger("shooter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SHOTS_DIR = os.getenv("SHOTS_DIR", "/data/screenshots")
WIDTH = int(os.getenv("SHOT_WIDTH", "1280"))
HEIGHT = int(os.getenv("SHOT_HEIGHT", "800"))
QUALITY = int(os.getenv("SHOT_QUALITY", "72"))
THUMB_W = int(os.getenv("SHOT_THUMB_WIDTH", "360"))      # miniatura per la lista siti
NAV_TIMEOUT = int(os.getenv("SHOT_TIMEOUT_MS", "25000"))
SETTLE_MS = int(os.getenv("SHOT_SETTLE_MS", "1800"))     # respiro prima dello scatto
VIDEO_WAIT_MS = int(os.getenv("SHOT_VIDEO_WAIT_MS", "6000"))

app = FastAPI(title="Sentinel shooter")

_browser = None
_lock = asyncio.Lock()      # una cattura per volta: Chromium in container e' pesante


@app.on_event("startup")
async def _startup():
    global _browser
    os.makedirs(SHOTS_DIR, exist_ok=True)
    pw = await async_playwright().start()
    # --autoplay-policy: senza, i video di sfondo non partono in automatico e restano neri
    args = ["--no-sandbox", "--disable-dev-shm-usage",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=IsolateOrigins,site-per-process"]
    try:
        # Chrome ha i codec H.264/AAC: indispensabile per i video di sfondo in MP4
        _browser = await pw.chromium.launch(channel="chrome", args=args)
        engine = "Google Chrome (codec H.264 disponibili)"
    except Exception as ex:  # noqa: BLE001
        _browser = await pw.chromium.launch(args=args)
        engine = "Chromium senza codec proprietari: i video MP4 resteranno vuoti"
        log.warning("Chrome non disponibile (%s)", str(ex)[:160])
    log.info("shooter pronto (%sx%s, q=%s) - %s", WIDTH, HEIGHT, QUALITY, engine)


@app.get("/healthz")
async def healthz():
    return {"ok": _browser is not None}


@app.post("/shot")
async def shot(payload: dict = Body(...)):
    url = str(payload.get("url", "")).strip()
    site_id = int(payload.get("site_id") or 0)
    if not url.startswith(("http://", "https://")) or site_id <= 0:
        raise HTTPException(422, "url e site_id obbligatori")

    name = f"site_{site_id}.jpg"                 # nome fisso: niente path arbitrari
    dest = os.path.join(SHOTS_DIR, name)
    tmp = dest + ".tmp"
    thumb = os.path.join(SHOTS_DIR, f"site_{site_id}_thumb.jpg")

    async with _lock:
        ctx = await _browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            ignore_https_errors=True,            # certificati interni/hairpin non devono bloccare
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36 SentinelTD/1.0"),
            locale="it-IT",
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT)
        except Exception:                        # noqa: BLE001
            # se "networkidle" non arriva (chat widget, polling, pubblicita') scatto lo stesso:
            # meglio un'anteprima imperfetta che nessuna anteprima
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
        try:
            # banner cookie piu' diffusi: tolgono meta' pagina all'anteprima
            await page.add_style_tag(content="""
                #cookie-law-info-bar, .cli-modal, #CybotCookiebotDialog, #onetrust-consent-sdk,
                .iubenda-cs-container, #iubenda-cs-banner, .cmplz-cookiebanner, #cmplz-cookiebanner-container,
                .moove_gdpr_cookie_modal, #moove_gdpr_cookie_info_bar, .cc-window, #usercentrics-root
                { display: none !important; }
                html { scroll-behavior: auto !important; }
            """)

            # 1) sveglia le immagini lazy: molti temi le caricano solo allo scroll
            await page.evaluate("""() => {
                window.scrollTo(0, window.innerHeight);
                window.scrollTo(0, 0);
                document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
            }""")

            # 2) VIDEO: senza questo passaggio l'area del video resta BIANCA nello
            # screenshot, perche' al momento dello scatto non e' ancora stato disegnato
            # nessun frame. Li avviamo muti, aspettiamo che abbiano dati, e forziamo un
            # frame spostando leggermente currentTime.
            await page.evaluate("""async () => {
                const vids = Array.from(document.querySelectorAll('video'));
                await Promise.all(vids.map(async v => {
                    try {
                        v.muted = true; v.defaultMuted = true; v.playsInline = true;
                        v.setAttribute('playsinline', '');
                        if (v.preload === 'none') { v.preload = 'auto'; v.load(); }
                        await v.play().catch(() => {});
                    } catch (e) {}
                }));
            }""")
            try:
                # aspetta che ogni video abbia almeno un frame disponibile (o sia in errore)
                await page.wait_for_function(
                    """() => Array.from(document.querySelectorAll('video'))
                            .every(v => v.readyState >= 2 || v.error || v.networkState === 3)""",
                    timeout=VIDEO_WAIT_MS)
            except Exception:  # noqa: BLE001
                pass
            await page.evaluate("""() => {
                document.querySelectorAll('video').forEach(v => {
                    try {
                        // un piccolo salto costringe il browser a disegnare il frame
                        if (v.readyState >= 2 && v.currentTime < 0.05) v.currentTime = 0.1;
                        // ripieghi se il video resta senza frame (codec mancante, rete, formato):
                        if (v.readyState < 2) {
                            if (v.poster) {
                                // 1) usa il poster dichiarato
                                v.style.background = 'url("' + v.poster + '") center center / cover no-repeat';
                            } else {
                                // 2) niente poster: nascondi il video e lascia emergere lo sfondo
                                //    della sezione (immagine o colore), meglio di un rettangolo vuoto
                                v.style.visibility = 'hidden';
                            }
                        }
                    } catch (e) {}
                });
            }""")

            # 3) respiro finale per font, animazioni d'ingresso e frame video
            await page.wait_for_timeout(SETTLE_MS)
            await page.screenshot(path=tmp, type="jpeg", quality=QUALITY, full_page=False,
                                  animations="disabled")
        finally:
            await page.close()
            await ctx.close()

    os.replace(tmp, dest)                        # scrittura atomica: mai un file mezzo scritto

    # Miniatura per la lista siti: senza, il pannello caricherebbe 40+ screenshot a
    # piena risoluzione a ogni apertura (megabyte inutili). ~10 KB l'una.
    try:
        from PIL import Image
        with Image.open(dest) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_W, THUMB_W), Image.LANCZOS)
            im.save(thumb + ".tmp", "JPEG", quality=68, optimize=True)
        os.replace(thumb + ".tmp", thumb)
    except Exception as ex:  # noqa: BLE001
        log.warning("miniatura non creata per %s: %s", name, ex)

    log.info("screenshot ok: %s (%s)", name, url)
    return {"path": name}

#!/usr/bin/env python3
"""
Costruisce gli zip installabili dei connettori a partire dai sorgenti in connectors/.

La chiave di registrazione automatica NON e' nel repository: viene inserita solo
nello zip WordPress generato, leggendola da variabile d'ambiente o da argomento.

    SENTINEL_HUB_URL=https://sentinel.tuodominio.it SENTINEL_HUB_KEY=la-tua-chiave \
        python scripts/build_connectors.py
    python scripts/build_connectors.py --url https://sentinel.tuodominio.it --key la-tua-chiave
    python scripts/build_connectors.py            # pacchetto neutro (si configura dal sito)

Di norma non serve: il pannello genera da solo il pacchetto WordPress gia' configurato
(Impostazioni > Connettori). Questo script serve per build fuori dal pannello.

Output in dist/ (esclusa da Git):
    dist/sentinel-td-wp-<versione>.zip   -> WordPress: Plugin > Aggiungi nuovo > Carica
    dist/sentinel-td-jm-<versione>.zip   -> Joomla: Sistema > Installa > Estensioni
"""
import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WP_DIR = ROOT / "connectors" / "wordpress" / "td-panopticon"
JM_DIR = ROOT / "connectors" / "joomla" / "plg_system_tdpanopticon"
DIST = ROOT / "dist"
KEY_RE = re.compile(r"const TDPANOP_HUB_KEY = '[^']*';")
URL_RE = re.compile(r"const TDPANOP_HUB_URL = '[^']*';")


def _php_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _files(base: Path):
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.name != ".DS_Store" and "__MACOSX" not in p.parts:
            yield p


def build_wp(key: str, url: str = "") -> Path:
    main = WP_DIR / "td-panopticon.php"
    src = main.read_text("utf-8")
    ver = re.search(r"^\s*\*\s*Version:\s*([\w.\-]+)", src, re.M).group(1)
    if not KEY_RE.search(src):
        sys.exit("Costante TDPANOP_HUB_KEY non trovata nel connettore WordPress")
    out = DIST / f"sentinel-td-wp-{ver}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _files(WP_DIR):
            arc = "td-panopticon/" + f.relative_to(WP_DIR).as_posix()
            if f == main:
                # indirizzo e chiave entrano SOLO nello zip, il sorgente resta pulito
                out_src = KEY_RE.sub(f"const TDPANOP_HUB_KEY = '{_php_quote(key)}';", src, count=1)
                out_src = URL_RE.sub(f"const TDPANOP_HUB_URL = '{_php_quote(url.rstrip('/'))}';", out_src, count=1)
                z.writestr(arc, out_src)
            else:
                z.write(f, arc)
    return out


def build_joomla() -> Path:
    xml = (JM_DIR / "tdpanopticon.xml").read_text("utf-8")
    ver = re.search(r"<version>([\w.\-]+)</version>", xml).group(1)
    out = DIST / f"sentinel-td-jm-{ver}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _files(JM_DIR):
            z.write(f, f.relative_to(JM_DIR).as_posix())   # manifest alla radice dello zip
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build dei connettori Sentinel TD")
    ap.add_argument("--key", default=os.environ.get("SENTINEL_HUB_KEY", ""),
                    help="chiave di registrazione (default: variabile SENTINEL_HUB_KEY)")
    ap.add_argument("--url", default=os.environ.get("SENTINEL_HUB_URL", ""),
                    help="indirizzo pubblico del pannello (default: variabile SENTINEL_HUB_URL)")
    args = ap.parse_args()
    DIST.mkdir(exist_ok=True)
    wp = build_wp(args.key.strip(), args.url.strip())
    jm = build_joomla()
    conf = [x for x, v in (("indirizzo", args.url.strip()), ("chiave", args.key.strip())) if v]
    print(f"WordPress: {wp.relative_to(ROOT)}" + (f"  ({' e '.join(conf)} nel pacchetto)" if conf else "  (neutro: si configura dal sito)"))
    print(f"Joomla:    {jm.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

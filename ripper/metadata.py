"""Book lookup (Audible catalog and Apple Books) and cover handling."""
import html
import json
import os
import re
import urllib.parse
import urllib.request

from . import config
from .util import chown, log, run

UA = "Mozilla/5.0 (audiobook-ripper)"
AUDIBLE_TLD = {"us": "com", "uk": "co.uk", "ca": "ca", "au": "com.au", "de": "de", "fr": "fr",
               "it": "it", "es": "es", "in": "in", "jp": "co.jp"}


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _strip_html(s):
    s = re.sub(r"<\s*(br|/p|/li)\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def search_audible(q):
    tld = AUDIBLE_TLD.get(config.AUDIBLE_REGION, "com")
    params = urllib.parse.urlencode({
        "keywords": q, "num_results": 12, "products_sort_by": "Relevance",
        "response_groups": "contributors,media,product_attrs,product_desc,product_extended_attrs,series",
        "image_sizes": "500,1024",
    })
    data = _get_json(f"https://api.audible.{tld}/1.0/catalog/products?{params}")
    out = []
    for p in data.get("products", []):
        series = (p.get("series") or [{}])[0]
        imgs = p.get("product_images") or {}
        out.append({
            "source": "Audible",
            "title": p.get("title", ""),
            "subtitle": p.get("subtitle", "") or "",
            "author": ", ".join(a.get("name", "") for a in p.get("authors") or []),
            "narrator": ", ".join(a.get("name", "") for a in p.get("narrators") or []),
            "series": series.get("title", "") or "",
            "series_seq": str(series.get("sequence", "") or ""),
            "year": (p.get("release_date") or "")[:4],
            "publisher": p.get("publisher_name", "") or "",
            "description": _strip_html(p.get("publisher_summary") or p.get("merchandising_summary") or ""),
            "cover_url": imgs.get("1024") or imgs.get("500") or "",
            "minutes": p.get("runtime_length_min"),
        })
    return out


def search_apple(q):
    params = urllib.parse.urlencode({"term": q, "media": "audiobook", "entity": "audiobook",
                                     "country": config.ITUNES_COUNTRY, "limit": 12})
    data = _get_json(f"https://itunes.apple.com/search?{params}")
    out = []
    for p in data.get("results", []):
        art = p.get("artworkUrl100", "") or ""
        out.append({
            "source": "Apple Books",
            "title": p.get("collectionName", "") or "",
            "subtitle": "",
            "author": p.get("artistName", "") or "",
            "narrator": "",
            "series": "",
            "series_seq": "",
            "year": (p.get("releaseDate") or "")[:4],
            "publisher": "",
            "description": _strip_html(p.get("description", "")),
            "cover_url": art.replace("100x100bb", "1000x1000bb"),
            "minutes": round(p["trackTimeMillis"] / 60000) if p.get("trackTimeMillis") else None,
        })
    return out


def search(q):
    results, errors = [], []
    for name, fn in (("Audible", search_audible), ("Apple Books", search_apple)):
        try:
            results += fn(q)
        except Exception as e:
            log.warning("%s search failed: %s", name, e)
            errors.append(f"{name}: {e}")
    return results, errors


def save_cover_bytes(data, dest):
    """Normalise any image to a JPEG no wider than 1400px."""
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Image is too large (20 MB max)")
    raw = dest + ".src"
    with open(raw, "wb") as f:
        f.write(data)
    r = run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", raw,
             "-vf", "scale='min(1400,iw)':-2", "-frames:v", "1", "-q:v", "3", dest])
    os.remove(raw)
    if r.returncode != 0:
        raise ValueError("That file doesn't look like an image")
    chown(dest)


def fetch_cover(url, dest):
    if not re.match(r"^https?://", url or ""):
        raise ValueError("Cover link must start with http:// or https://")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(20 * 1024 * 1024 + 1)
    save_cover_bytes(data, dest)

"""Illustrations libres (vidéos, photos, musique) depuis des banques gratuites, quand ses propres plans ne suffisent pas.

En deux temps, pour ne télécharger que ce qui est choisi :
  1. python scripts/illustrer.py chercher .monteur/idees.json
     idees.json (écrit par Claude) : [{"id": "dubai", "mot": "Dubaï", "phrase": "…", "duree_min": 2,
       "type": "video", "requetes": ["Dubai skyline night timelapse", "Dubai marina boats sunset", "…"]}, ...]
     → planche .monteur/candidats_1.jpg (4 à 6 candidats par idée, « dubai-2 ▶ 8,4 s ▯ pexels · Pexels »,
       vertical d'abord) + .monteur/candidats.json
  2. python scripts/illustrer.py choisir dubai-2 maurice-1 ... [--broll B-Roll]
     → <dossier b-roll>/libres/dubai-2.mp4… (jamais d'écrasement) + libres/CREDITS.md (titre, auteur, source,
       licence) + .monteur/credits.txt (lignes à coller sous le post) + entrées b-roll et commande plans.py à lancer
  Musique : python scripts/illustrer.py musique "chill lounge" → candidats ; choisir musique-2 → musique/
  Clés (gratuites, facultatives : plus de vidéos verticales) : python scripts/illustrer.py cle pexels <clé>
  (ou PEXELS_API_KEY / PIXABAY_API_KEY)

Sources sans clé : Wikimedia Commons (vidéos et photos), NASA (vidéos, domaine public), Openverse (photos, musique) ;
seulement CC0, domaine public et CC BY (attribution dans les crédits). Avec clé : Pexels et Pixabay (sans attribution).
Réponses des API gardées 24 h dans ~/.monteur/cache/ (exigé par Pixabay)."""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from common import BROLL, MUSIQUE, VIDEO_EXT, die, dossier, work_dir  # noqa: E402

UA = {"User-Agent": "monteur-plugin/0.4 (https://github.com/LarryJr64/monteur; Claude Code video editing plugin)"}
MONTEUR = Path.home() / ".monteur"
CLES = MONTEUR / "cles.json"
CACHE = MONTEUR / "cache"
CACHE_S = 24 * 3600   # Pixabay exige de garder les réponses 24 h (et de ne pas les redemander)
PAR_IDEE = 6          # candidats montrés par idée (au plus)
TIMEOUT = 20
MUSIQUE_MIN = 30      # s
VIDEO_MIN, VIDEO_MAX = 3, 60  # s : plus long = conférence, interview, pas un plan d'illustration
LICENCES_OK = ("cc0", "pdm", "by")  # pas de BY-SA (partage à l'identique) ni NC (pas d'usage commercial)
FONT = Path(__file__).parent / "fonts" / "Montserrat-ExtraBold.ttf"  # a le ▶ (pas Poppins)
MEDIA_EXT = VIDEO_EXT | {".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav", ".ogg", ".flac", ".m4a"}
NOMS = {"pexels": "Pexels", "pixabay": "Pixabay", "wikimedia": "Wikimedia Commons", "openverse": "Openverse",
        "nasa": "NASA"}
LIENS = {"pexels": "https://www.pexels.com/license/", "pixabay": "https://pixabay.com/service/license-summary/",
         "nasa": "https://www.nasa.gov/nasa-brand-center/images-and-media/"}


def cles():
    d = {}
    if CLES.exists():
        try:
            d = json.loads(CLES.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    for nom in ("pexels", "pixabay"):
        if os.environ.get(f"{nom.upper()}_API_KEY"):
            d[nom] = os.environ[f"{nom.upper()}_API_KEY"]
    return d


def get_json(url, headers=None):
    f = CACHE / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")
    if f.is_file() and time.time() - f.stat().st_mtime < CACHE_S:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        d = json.loads(r.read().decode("utf-8"))
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d), encoding="utf-8")
    return d


def telecharger(url, dest, headers=None):
    part = Path(f"{dest}.part")  # un téléchargement coupé ne laisse jamais un fichier tronqué sous le vrai nom
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    os.replace(part, dest)
    return dest


def _fr(x):
    return f"{x:.1f}".replace(".", ",")


def vertical(c):
    return (c.get("h") or 0) > (c.get("w") or 0)


def court(lic):
    l = (lic or "").lower()
    for k, v in (("pexels", "Pexels"), ("pixabay", "Pixabay"), ("nasa", "DP"), ("cc0", "CC0"),
                 ("public domain", "DP"), ("domaine public", "DP"), ("pdm", "DP"), ("by", "CC BY")):
        if k in l:
            return v
    return lic or "?"


def legende(c):
    d = f" {_fr(c['duree'])} s" if c.get("duree") else ""
    return (f"{c['repere']} {'▶' if c['kind'] == 'video' else 'photo'}{d} {'▯' if vertical(c) else '▭'} "
            f"{c['source']} · {court(c.get('licence'))}")


# ---------- sources : chacune rend des candidats {kind, url, miniature, auteur, licence, page, w, h, duree} ----------

def _video_file(files):
    """Fichier vidéo le plus proche du 1080p vertical, sans dépasser (poids raisonnable)."""
    files = [f for f in files if f.get("link") or f.get("url")]
    if not files:
        return None
    def score(f):
        w, h = f.get("width") or 0, f.get("height") or 0
        return (h >= w, -abs(max(w, h) - 1920) if max(w, h) <= 1920 else -10000)
    return max(files, key=score)


def pexels(q, kind, key):
    h = {"Authorization": key}
    qs = urllib.parse.urlencode({"query": q, "per_page": PAR_IDEE, "orientation": "portrait"})
    lic = {"licence": "Pexels (libre, sans attribution)", "licence_url": LIENS["pexels"]}
    if kind == "video":
        d = get_json(f"https://api.pexels.com/videos/search?{qs}", h)
        out = []
        for v in d.get("videos", []):
            f = _video_file(v.get("video_files", []))
            pics = [p["picture"] for p in sorted(v.get("video_pictures") or [], key=lambda p: p.get("nr", 0))
                    if p.get("picture")]
            if f:
                out.append({"kind": "video", "url": f["link"], "miniature": v.get("image"), "auteur": v["user"]["name"],
                            "page": v.get("url"), "w": f.get("width"), "h": f.get("height"),
                            "duree": v.get("duration"), **lic,
                            # début / milieu / fin : on voit si le plan reste dans le sujet
                            **({"vignettes": [pics[0], pics[len(pics) // 2], pics[-1]]} if len(pics) >= 3 else {})})
        return out
    d = get_json(f"https://api.pexels.com/v1/search?{qs}", h)
    return [{"kind": "photo", "url": p["src"].get("large2x") or p["src"]["original"], "miniature": p["src"]["medium"],
             "auteur": p.get("photographer"), "page": p.get("url"), "titre": p.get("alt"),
             "w": p.get("width"), "h": p.get("height"), **lic} for p in d.get("photos", [])]


def pixabay(q, kind, key):
    lic = {"licence": "Pixabay (libre, sans attribution)", "licence_url": LIENS["pixabay"]}
    if kind == "video":
        qs = urllib.parse.urlencode({"key": key, "q": q, "per_page": max(PAR_IDEE, 3)})
        d = get_json(f"https://pixabay.com/api/videos/?{qs}")
        out = []
        for v in d.get("hits", []):
            # la plus grande taille sans dépasser 1920 (jamais « large » en 4K : lourd et inutile en 1080×1920)
            tailles = [f for f in (v.get("videos") or {}).values()
                       if f.get("url") and max(f.get("width") or 0, f.get("height") or 0) <= 1920]
            f = max(tailles, key=lambda f: max(f.get("width") or 0, f.get("height") or 0), default=None)
            if f:
                out.append({"kind": "video", "url": f["url"], "miniature": f.get("thumbnail"), "auteur": v.get("user"),
                            "page": v.get("pageURL"), "w": f.get("width"), "h": f.get("height"),
                            "duree": v.get("duration"), "titre": v.get("tags"), **lic})
        return out
    qs = urllib.parse.urlencode({"key": key, "q": q, "image_type": "photo", "orientation": "vertical",
                                 "per_page": max(PAR_IDEE, 3)})
    d = get_json(f"https://pixabay.com/api/?{qs}")
    return [{"kind": "photo", "url": p["largeImageURL"], "miniature": p.get("webformatURL"), "auteur": p.get("user"),
             "page": p.get("pageURL"), "titre": p.get("tags"),
             "w": p.get("imageWidth"), "h": p.get("imageHeight"), **lic} for p in d.get("hits", [])]


def _licence_wiki(nom):
    n = (nom or "").lower()
    if "sa" in n.split() or "-sa" in n or "nc" in n.replace("-", " ").split():
        return None
    if "cc0" in n or "public domain" in n or "domaine public" in n:
        return "cc0"
    if re.search(r"cc[ -]by(?![- ]?sa)", n):
        return "by"
    return None


def _texte(meta, champ):
    return re.sub("<[^>]+>", "", (meta.get(champ) or {}).get("value", "")).strip()


TAILLES_COMMONS = (330, 500, 960, 1280, 1920)  # largeurs standard de thumb.wikimedia.org (https://w.wiki/GHai)


def _mini_commons(u, largeur=330):
    """Image en largeur standard sur thumb.wikimedia.org : une autre taille → erreur 400, et l'original sur
    upload.wikimedia.org est vite refusé aux scripts (erreur 429)."""
    m = re.match(r"https://(?:upload|thumb)\.wikimedia\.org/wikipedia/commons/(?:thumb/)?(\w/\w\w/)([^/?]+)", u)
    return f"https://thumb.wikimedia.org/wikipedia/commons/thumb/{m[1]}{m[2]}/{largeur}px-{m[2]}" if m else u


def wikimedia(q, kind, _key=None):
    video = kind == "video"
    qs = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 10,
        "gsrsearch": f"{q} filetype:{'video' if video else 'bitmap'}",
        **({"prop": "videoinfo", "viprop": "url|size|extmetadata|derivatives", "viurlwidth": 400} if video else
           {"prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlheight": 1920})})
    d = get_json(f"https://commons.wikimedia.org/w/api.php?{qs}")
    out = []
    for p in sorted((d.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        ii = (p.get("videoinfo" if video else "imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        nom = _texte(meta, "LicenseShortName")
        if not _licence_wiki(nom) or not ii.get("thumburl"):
            continue
        c = {"kind": kind, "auteur": _texte(meta, "Artist") or "Wikimedia Commons", "licence": nom,
             "licence_url": _texte(meta, "LicenseUrl") or None, "page": ii.get("descriptionurl"),
             "titre": _texte(meta, "ObjectName") or p.get("title")}
        if video:
            # version 720p/1080p transcodée par Commons (l'original peut être en 4K ou en .ogv)
            der = [x for x in ii.get("derivatives") or [] if str(x.get("transcodekey", "")).startswith(("720p", "1080p"))]
            if not der:
                continue
            f = max(der, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0))
            c.update(url=f["src"], miniature=ii["thumburl"], w=f.get("width"), h=f.get("height"),
                     duree=ii.get("duration"))
        else:
            url = ii["thumburl"]
            if "//upload." in url:  # moins de 1920 de haut : Commons renvoie l'original → plus grande largeur standard
                url = _mini_commons(url, max((t for t in TAILLES_COMMONS if t <= (ii.get("width") or 0)), default=330))
            c.update(url=url, miniature=_mini_commons(url), w=ii.get("thumbwidth"), h=ii.get("thumbheight"))
        out.append(c)
    return out[:PAR_IDEE]


def _duree(s):
    """« 0:00:30 » ou « 12.50 s » (métadonnées exiftool de la NASA) → secondes."""
    s = str(s or "").strip()
    if ":" in s:
        return sum(float(x) * 60 ** i for i, x in enumerate(reversed(s.split(":"))))
    m = re.match(r"\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def nasa(q, kind, _key=None):
    """Vidéos de la NASA : domaine public (œuvre du gouvernement américain), sans logo ni aval implicite."""
    if kind != "video":
        return []
    qs = urllib.parse.urlencode({"q": q, "media_type": "video", "page_size": PAR_IDEE * 2})
    d = get_json(f"https://images-api.nasa.gov/search?{qs}")
    out = []
    for it in (d.get("collection") or {}).get("items", [])[:PAR_IDEE * 2]:
        x = (it.get("data") or [{}])[0]
        if x.get("photographer") and "nasa" not in x["photographer"].lower():
            continue  # filmé par un tiers (Rocket Lab…) : pas forcément du domaine public
        try:
            a = get_json(f"https://images-api.nasa.gov/asset/{urllib.parse.quote(x['nasa_id'])}")
            # des identifiants NASA ont des espaces : à encoder, sinon l'URL est refusée
            hrefs = [urllib.parse.quote(i.get("href", "").replace("http://", "https://", 1), safe=":/~%")
                     for i in a["collection"]["items"]]
            mp4 = next((h for k in ("~medium.mp4", "~mobile.mp4") for h in hrefs if h.endswith(k)), None)
            meta = get_json(next(h for h in hrefs if h.endswith("metadata.json")))
        except Exception:  # un élément sans fichier ou sans métadonnées : on passe au suivant
            continue
        mini = next((urllib.parse.quote(l["href"], safe=":/~%") for l in it.get("links", [])
                     if l.get("render") == "image"), None)
        if not mp4 or not mini:
            continue
        out.append({"kind": "video", "url": mp4, "miniature": mini, "auteur": x.get("photographer") or "NASA",
                    "licence": "Domaine public (NASA)", "licence_url": LIENS["nasa"], "titre": x.get("title"),
                    "page": f"https://images.nasa.gov/details/{urllib.parse.quote(x['nasa_id'])}",
                    "w": meta.get("QuickTime:ImageWidth"), "h": meta.get("QuickTime:ImageHeight"),
                    "duree": _duree(meta.get("QuickTime:Duration"))})
    return out


def openverse(q, kind, _key=None, media="images"):
    if kind == "video":
        return []
    qs = urllib.parse.urlencode({"q": q, "license": ",".join(LICENCES_OK), "page_size": PAR_IDEE + 2 if media == "images" else 20,
                                 **({"mature": "false"} if media == "images" else {"category": "music"})})
    d = get_json(f"https://api.openverse.org/v1/{media}/?{qs}")
    out = []
    for r in d.get("results", []):
        lic = f"CC {r.get('license', '').upper()} {r.get('license_version') or ''}".strip()
        c = {"kind": "photo" if media == "images" else "musique", "url": r.get("url"), "auteur": r.get("creator"),
             "licence": lic, "licence_url": r.get("license_url"), "page": r.get("foreign_landing_url"),
             "titre": r.get("title")}
        if media == "images":
            c.update(miniature=r.get("thumbnail") or r.get("url"), w=r.get("width"), h=r.get("height"))
        else:
            c.update(duree=(r.get("duration") or 0) / 1000, tags=[t.get("name") for t in r.get("tags", [])][:6])
        if c["url"] and (media == "images" or c["duree"] >= MUSIQUE_MIN):  # pas de boucle de 8 s ni de bip
            out.append(c)
    return out[:PAR_IDEE]


def candidats_idee(idee, k):
    """Vidéos d'abord si demandées, puis photos ; jusqu'à PAR_IDEE, sans doublon, durée utilisable, vertical d'abord."""
    kind = idee.get("type", "photo")
    ordre = []
    if kind == "video":
        ordre += [(pexels, "video", k.get("pexels")), (pixabay, "video", k.get("pixabay")),
                  (wikimedia, "video", True), (nasa, "video", True)]
    ordre += [(pexels, "photo", k.get("pexels")), (pixabay, "photo", k.get("pixabay")),
              (wikimedia, "photo", True), (openverse, "photo", True)]
    mini = max(idee.get("duree_min") or 0, VIDEO_MIN)
    out, vus = [], set()
    for q in idee.get("requetes") or [idee["id"]]:
        for src, kd, key in ordre:
            if not key or len(out) >= PAR_IDEE:
                continue
            try:
                for c in src(q, kd, key):
                    if c["kind"] == "video" and not mini <= (c.get("duree") or 0) <= VIDEO_MAX:
                        continue
                    # même durée au dixième = même clip (la NASA est aussi sur Commons)
                    dup = round(c["duree"], 1) if c["kind"] == "video" else None
                    if c["url"] not in vus and dup not in vus and len(out) < PAR_IDEE:
                        vus |= {c["url"], dup} - {None}
                        out.append(dict(c, source=src.__name__, requete=q))
            except Exception as e:  # réseau, quota, réponse inattendue : on passe à la source suivante
                print(f"  {src.__name__} « {q} » : {e}", flush=True)
    return sorted(out, key=lambda c: (c["kind"] != "video", not vertical(c)))


# ---------- planche ----------

def miniature(c, dest):
    """Miniature locale ; Pexels : bande début / milieu / fin (côte à côte si vertical, empilée si paysage)."""
    ims = []
    for i, u in enumerate(c.get("vignettes") or [c["miniature"]]):
        p = dest.with_name(f"{dest.stem}.{i}.tmp")
        telecharger(u, p)
        with Image.open(p) as im:
            ims.append(im.convert("RGB"))
        p.unlink()
    w, h = ims[0].size
    cote = vertical(c)
    band = Image.new("RGB", (w * len(ims), h) if cote else (w, h * len(ims)))
    for i, im in enumerate(ims):
        band.paste(im.resize((w, h)), (i * w, 0) if cote else (0, i * h))
    band.save(dest, quality=85)


def planche(cands, out_dir, wd):
    """Miniatures légendées (repère, durée, orientation, source, licence) ; une ligne par idée de 6, 12 par planche."""
    f = ImageFont.truetype(str(FONT), 22)
    tw, th, cols, bas = 300, 420, PAR_IDEE, 62
    chemins = []
    for p0 in range(0, len(cands), 2 * cols):
        lot = cands[p0:p0 + 2 * cols]
        sheet = Image.new("RGB", (tw * min(cols, len(lot)), (th + bas) * ((len(lot) + cols - 1) // cols)), "white")
        d = ImageDraw.Draw(sheet)
        for i, c in enumerate(lot):
            x, y = (i % cols) * tw, (i // cols) * (th + bas)
            try:
                im = Image.open(out_dir / c["miniature_locale"]).convert("RGB")
                im.thumbnail((tw - 10, th - 10))
                sheet.paste(im, (x + (tw - im.width) // 2, y + 5 + (th - 10 - im.height) // 2))
            except Exception:
                d.rectangle((x + 5, y + 5, x + tw - 5, y + th - 5), fill="#ddd")
            haut = f"{c['repere']} ▶ {_fr(c['duree'])} s" if c["kind"] == "video" and c.get("duree") else \
                f"{c['repere']} {'▶' if c['kind'] == 'video' else 'photo'}"
            d.text((x + 8, y + th + 2), haut, font=f, fill="black")
            bx, by = x + 8, y + th + 34  # ▯ / ▭ absents des polices fournies : dessinés
            d.rectangle((bx, by, bx + 12, by + 20) if vertical(c) else (bx, by + 4, bx + 20, by + 16),
                        outline="black", width=2)
            d.text((bx + 28, y + th + 30), f"{c['source']} · {court(c.get('licence'))}", font=f, fill="black")
        p = wd / f"candidats_{p0 // (2 * cols) + 1}.jpg"
        sheet.save(p, quality=85)
        chemins.append(p)
    return chemins


def chercher(idees_path, root):
    wd = work_dir(root)
    idees = json.loads(Path(idees_path).read_text(encoding="utf-8"))
    k = cles()
    if not (k.get("pexels") or k.get("pixabay")):
        print("Sans clé : vidéos Wikimedia Commons et NASA, photos Wikimedia et Openverse. Plus de vidéos "
              "verticales avec une clé gratuite : illustrer.py cle pexels <clé> (pexels.com/api).")
    mini = wd / "candidats"
    mini.mkdir(exist_ok=True)
    tous = []
    for idee in idees:
        cs = candidats_idee(idee, k)
        if not cs:
            print(f"{idee['id']} : aucun candidat (autres mots, en anglais, filmables) → pas d'illustration")
        elif idee.get("type") == "video" and not any(c["kind"] == "video" for c in cs):
            print(f"{idee['id']} : aucune vidéo trouvée, photos seulement"
                  + ("" if k.get("pexels") or k.get("pixabay") else " (une clé Pexels gratuite donne des vidéos)"))
        for n, c in enumerate(cs, 1):
            c.update(repere=f"{idee['id']}-{n}", idee=idee["id"], lieu=idee.get("lieu"), mot=idee.get("mot"),
                     phrase=idee.get("phrase"))
            try:
                miniature(c, mini / f"{c['repere']}.jpg")
                c["miniature_locale"] = f"{c['repere']}.jpg"
            except Exception as e:
                print(f"  miniature {c['repere']} : {e}")
            print(f"  {legende(c)}")
            tous.append(c)
        print(f"{idee['id']} : {len(cs)} candidat(s)")
    (wd / "candidats.json").write_text(json.dumps(tous, ensure_ascii=False, indent=1), encoding="utf-8")
    if tous:
        for p in planche(tous, mini, wd):
            print(f"planche : {p}")
    return tous


def musique(requete, root):
    wd = work_dir(root)
    try:
        cs = openverse(requete, "musique", True, media="audio")
    except Exception as e:
        die(f"recherche de musique impossible : {e}")
    tous = json.loads((wd / "candidats.json").read_text(encoding="utf-8")) if (wd / "candidats.json").exists() else []
    tous = [c for c in tous if c.get("idee") != "musique"]
    for n, c in enumerate(cs, 1):
        c.update(repere=f"musique-{n}", idee="musique", source="openverse")
        print(f"{c['repere']} : « {c.get('titre')} » — {c.get('auteur')}, {round(c.get('duree') or 0)} s, "
              f"{c['licence']}, tags {', '.join(t for t in c.get('tags') or [] if t)}")
    (wd / "candidats.json").write_text(json.dumps(tous + cs, ensure_ascii=False, indent=1), encoding="utf-8")
    return cs


# ---------- choix, crédits ----------

def libre(dest):
    """Nom libre : jamais d'écrasement d'un fichier existant (dubai-2.mp4 → dubai-2_2.mp4…)."""
    out, k = dest, 2
    while out.exists():
        out, k = dest.with_name(f"{dest.stem}_{k}{dest.suffix}"), k + 1
    return out


def credit_post(c):
    """Ligne à coller sous le post : obligatoire en CC BY (titre, auteur, source, licence, modification)."""
    quoi = {"video": "Vidéo", "musique": "Musique"}.get(c["kind"], "Photo")
    src = NOMS.get(c["source"], c["source"])
    lic = court(c.get("licence"))
    if lic == "CC BY":
        lien = f" ({c['licence_url']})" if c.get("licence_url") else ""
        return (f"{quoi} : « {c.get('titre') or 'sans titre'} » par {c.get('auteur') or '?'}, {c.get('licence')}{lien}, "
                f"via {src} — { {'musique': 'raccourcie', 'photo': 'recadrée'}.get(c['kind'], 'recadrée et raccourcie')}")
    if lic in ("CC0", "DP"):
        return f"{quoi} : {c.get('auteur') or src} (domaine public), via {src}"
    return f"{quoi} : {c.get('auteur') or '?'} via {src}"  # Pexels / Pixabay : par courtoisie


def _ajoute(path, lignes):
    old = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    path.write_text("\n".join(old + [l for l in lignes if l not in old]) + "\n", encoding="utf-8")


def choisir(reperes, root, broll=None):
    root = Path(root)
    wd = work_dir(root)
    tous = {c["repere"]: c for c in json.loads((wd / "candidats.json").read_text(encoding="utf-8"))}
    inconnus = [r for r in reperes if r not in tous]
    if inconnus:  # avant tout téléchargement : pas de fichier sans crédit
        die(f"repère(s) inconnu(s) : {', '.join(inconnus)} (voir .monteur/candidats.json)")
    libres = dossier(root, BROLL, broll) / "libres"
    md, post, desc, faits = [], [], [], []
    for r in reperes:
        c = tous[r]
        d = dossier(root, MUSIQUE) if c["kind"] == "musique" else libres
        d.mkdir(parents=True, exist_ok=True)
        ext = Path(urllib.parse.urlparse(c["url"]).path).suffix.lower()
        ext = ext if ext in MEDIA_EXT else {"video": ".mp4", "musique": ".mp3"}.get(c["kind"], ".jpg")
        dest = libre(d / f"{r}{ext}")
        try:
            telecharger(c["url"], dest)
        except Exception as e:  # les autres choix passent quand même, chacun avec son crédit
            print(f"{r} : téléchargement impossible ({e})")
            continue
        rel = dest.relative_to(root).as_posix()
        md.append(f"- `{rel}` : « {c.get('titre') or 'sans titre'} » — {c.get('auteur') or '?'} — "
                  f"{NOMS.get(c['source'], c['source'])} — {c.get('licence')}"
                  + (f" ({c['licence_url']})" if c.get("licence_url") else "")
                  + f" — {c.get('page') or c['url']} — recadré et raccourci")
        post.append(credit_post(c))
        faits.append({"fichier": rel, "kind": c["kind"], "lieu": c.get("lieu")})
        print(f"{r} → {rel}")
        if c["kind"] != "musique":
            entree = {"source": rel, "mot": c.get("mot") or "", "libre": True}
            if not vertical(c):
                entree["zoom_center"] = [0.5, 0.5]  # paysage : 0.25 / 0.5 / 0.75 selon où est le sujet
            print("  b-roll : " + json.dumps(entree, ensure_ascii=False))
        if c["kind"] == "video":
            desc.append(f"{rel}: illustration libre, {c.get('requete')}" + (f" — {c['titre']}" if c.get("titre") else ""))
    if md:
        libres.mkdir(parents=True, exist_ok=True)
        f = libres / "CREDITS.md"
        if not f.exists():
            f.write_text("# Crédits des illustrations libres\n\n", encoding="utf-8")
        _ajoute(f, md)
        _ajoute(wd / "credits.txt", post)
        print(f"crédits : {(libres / 'CREDITS.md').relative_to(root).as_posix()} ; à coller sous le post : "
              ".monteur/credits.txt (CC BY obligatoire ; Pexels/Pixabay par courtoisie)")
    if desc:
        _ajoute(wd / "descriptions.txt", desc)
        videos = [l.split(": ", 1)[0] for l in desc]
        try:  # repasser aussi les fichiers déjà indexés : leurs numéros P1, P2… ne bougent pas
            deja = list(dict.fromkeys(v["source"] for v in
                                      json.loads((wd / "plans.json").read_text(encoding="utf-8"))["plans"].values()))
        except (OSError, ValueError, KeyError):
            deja = []
        args = [a for a in deja if a not in videos] + videos
        print("Ensuite : python plans.py " + " ".join(f'"{a}"' if " " in a else a for a in args))
    return faits


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["chercher", "choisir", "musique", "cle"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--root", default=".")
    ap.add_argument("--broll", help="son dossier de b-roll (défaut : B-Roll, b-roll, secondaires ou broll)")
    a = ap.parse_args(argv)
    if a.action == "cle":
        if len(a.args) != 2 or a.args[0] not in ("pexels", "pixabay"):
            die("usage : illustrer.py cle pexels|pixabay <clé>")
        d = {}
        if CLES.exists():  # le fichier seul : jamais les clés des variables d'environnement
            try:
                d = json.loads(CLES.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        d[a.args[0]] = a.args[1]
        CLES.parent.mkdir(parents=True, exist_ok=True)
        CLES.write_text(json.dumps(d), encoding="utf-8")
        print(f"clé {a.args[0]} enregistrée ({CLES})")
    elif a.action == "chercher":
        chercher(a.args[0] if a.args else ".monteur/idees.json", a.root)
    elif a.action == "musique":
        musique(" ".join(a.args) or "chill background", a.root)
    else:
        choisir(a.args, a.root, a.broll)


if __name__ == "__main__":
    main()

"""edl.json → sorties/<stem>.fcpxml (Final Cut / DaVinci), <stem>.xml (Premiere xmeml v4), <stem>.srt.

Les clips pointent vers les rushs ORIGINAUX : on peut tout retoucher dans le logiciel.
Mêmes coupes que render.py : `.monteur/edl.rendu.json` (écrit par pacing/render) s'il est à jour, sinon
pacing.normalize_edl, puis captions.snapped_segments : mêmes temps partout.
Pistes son : 1 voix, 2 musique (bouclée, au volume de l'edl), 3 bruitages (assets/sfx, 4 images avant l'image visée).
Zooms : images-clés d'échelle dans le FCPXML (doux/punch/crash) ; Premiere reçoit l'échelle finale (statique).
shortcut: le recadrage sur le visage (suivi) n'est pas exporté, le cadre est centré ; à ajouter si demandé.
"""
import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent))
from common import die, probe  # noqa: E402
import captions  # noqa: E402
import reframe as rf  # noqa: E402

try:
    import pacing
except ImportError:
    pacing = None

SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "sfx"


def rt(frames, fps):
    """Temps rationnel FCPXML : `frames` images à `fps` (Fraction) → « 1001/30000s »."""
    t = Fraction(frames) / fps
    return "0s" if t == 0 else f"{t.numerator}/{t.denominator}s" if t.denominator != 1 else f"{t.numerator}s"


def sub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def zoom_keys(s, prev, fps, zp):
    """Courbe de zoom du segment (même calcul que render) → images-clés [(image, échelle)] et échelle finale."""
    kind = s.get("zoom_type") or ("doux" if zp.get("style") == "doux" and s.get("zoom", 1.0) > 1.0 else None)
    curve = rf.segment_scales(s, kind, prev, float(fps), zp.get("rampe_s", 0.5))
    if curve[0] == curve[-1]:
        return [], curve[-1]
    ramp = next((i for i, z in enumerate(curve) if z == curve[-1]), len(curve) - 1)
    return [(0, curve[0]), (ramp, curve[-1])], curve[-1]


def clips(edl, root, segs, fps, profile=None):
    """Liste à plat, en images de la séquence : segments (piste 1), b-roll (piste 2),
    musique (piste son 2, `audio`) et bruitages (piste son 3)."""
    out, prev, zp = [], 1.0, (profile or {}).get("zooms") or {}
    rf.doux_u(segs, zp.get("style", "cut"))
    for s in segs:
        keys, prev = zoom_keys(s, prev, fps, zp)
        out.append({"src": (root / s["source"]).resolve(), "track": 1, "at": round(s["offset"] * fps),
                    "in": round(s["start"] * fps), "len": s["frames"], "zoom": prev, "keys": keys})
    for b in edl.get("broll") or []:
        out.append({"src": (root / b["source"]).resolve(), "track": 2, "at": round(b["at"] * fps),
                    "in": round(b.get("source_start", 0) * fps), "len": round(b["duration"] * fps), "zoom": 1.0})
    for c in out:
        if not c["src"].exists():
            die(f"média introuvable : {c['src']}")
    total = sum(s["frames"] for s in segs)
    music = edl.get("music") or {}
    if music.get("source"):
        src = (root / music["source"]).resolve()
        if not src.exists():
            die(f"musique introuvable : {src}")
        n = max(1, round(probe(src)["duration"] * fps))
        vol = music.get("volume_db", ((profile or {}).get("audio") or {}).get("musique_db_sous_voix", -18))
        for at in range(0, total, n):  # bouclée jusqu'à la fin, comme au rendu
            out.append({"src": src, "track": "musique", "at": at, "in": 0, "len": min(n, total - at), "gain_db": vol})
    for x in edl.get("sfx") or []:
        src = SFX_DIR / f"{x.get('type', '')}.wav"
        if not src.exists():
            print(f"bruitage « {x.get('type')} » absent de assets/sfx : non exporté.")
            continue
        at = max(0, round(float(x["at"]) * fps) - 4)  # le son part ~4 images avant l'image visée
        n = min(max(1, round(probe(src)["duration"] * fps)), total - at)
        if n > 0:
            out.append({"src": src.resolve(), "track": "sfx", "at": at, "in": 0, "len": n,
                        "gain_db": x.get("volume_db", -14)})
    return out


def fcpxml(items, infos, w, h, fps, name):
    root = ET.Element("fcpxml", version="1.9")
    res = sub(root, "resources")
    sub(res, "format", id="r0", frameDuration=rt(1, fps), width=w, height=h, colorSpace="1-1-1 (Rec. 709)")
    ids = {}
    for i, (src, inf) in enumerate(infos.items(), 1):
        if "width" not in inf:  # musique / bruitage : asset son seul
            a = sub(res, "asset", id=f"a{i}", name=src.stem, start="0s", duration=rt(round(inf["duration"] * fps), fps),
                    hasVideo="0", hasAudio="1", audioSources="1", audioChannels="2", audioRate="48000")
            sub(a, "media-rep", kind="original-media", src=src.as_uri())
            ids[src] = f"a{i}"
            continue
        sfps = Fraction(inf.get("fps") or float(fps)).limit_denominator(1001)
        sub(res, "format", id=f"f{i}", frameDuration=rt(1, sfps), width=inf["width"], height=inf["height"])
        a = sub(res, "asset", id=f"a{i}", name=src.stem, start="0s", duration=rt(round(inf["duration"] * fps), fps),
                hasVideo="1", hasAudio="1" if inf["has_audio"] else "0", format=f"f{i}",
                audioSources="1", audioChannels="2", audioRate="48000")
        sub(a, "media-rep", kind="original-media", src=src.as_uri())
        ids[src] = f"a{i}"
    total = sum(c["len"] for c in items if c["track"] == 1)
    seq = sub(sub(sub(sub(root, "library"), "event", name="Monteur"), "project", name=name), "sequence",
              format="r0", duration=rt(total, fps), tcStart="0s", tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = sub(seq, "spine")
    mains = []
    for c in (c for c in items if c["track"] == 1):
        e = sub(spine, "asset-clip", ref=ids[c["src"]], name=c["src"].stem, offset=rt(c["at"], fps),
                start=rt(c["in"], fps), duration=rt(c["len"], fps), tcFormat="NDF")
        sub(e, "adjust-conform", type="fill")  # recadrage « cover » 9:16
        if c.get("keys"):
            # images-clés dans le temps du clip (qui commence à `start`) ; « ease » = rampe aisée comme au rendu
            ka = sub(sub(sub(e, "adjust-transform"), "param", name="scale"), "keyframeAnimation")
            for f, z in c["keys"]:
                sub(ka, "keyframe", time=rt(c["in"] + f, fps), value=f"{z:.4g} {z:.4g}", interp="ease")
        elif c["zoom"] != 1.0:
            sub(e, "adjust-transform", scale=f"{c['zoom']:g} {c['zoom']:g}")
        mains.append((c, e))
    lanes = {2: "1", "musique": "-1", "sfx": "-2"}
    for b in (c for c in items if c["track"] in lanes):
        host = next(((c, e) for c, e in mains if c["at"] <= b["at"] < c["at"] + c["len"]), None)
        if not host:
            continue
        c, e = host
        # un clip connecté est placé dans le temps source de son clip parent
        bb = sub(e, "asset-clip", ref=ids[b["src"]], lane=lanes[b["track"]], name=b["src"].stem,
                 offset=rt(c["in"] + b["at"] - c["at"], fps), start=rt(b["in"], fps), duration=rt(b["len"], fps),
                 tcFormat="NDF")
        if b["track"] == 2:
            sub(bb, "adjust-conform", type="fill")
        else:
            sub(bb, "adjust-volume", amount=f"{b['gain_db']:g}dB")
    return root


def xmeml(items, infos, w, h, fps, name):
    tb, ntsc = round(float(fps)), "TRUE" if fps.denominator == 1001 else "FALSE"

    def rate(parent, base=tb, nt=ntsc):
        r = sub(parent, "rate")
        sub(r, "timebase", base)
        sub(r, "ntsc", nt)

    root = ET.Element("xmeml", version="4")
    seq = sub(root, "sequence", id="sequence-1")
    sub(seq, "name", name)
    total = sum(c["len"] for c in items if c["track"] == 1)
    sub(seq, "duration", total)
    rate(seq)
    media = sub(seq, "media")
    video = sub(media, "video")
    sc = sub(sub(video, "format"), "samplecharacteristics")
    rate(sc)
    for k, v in (("width", w), ("height", h), ("anamorphic", "FALSE"), ("pixelaspectratio", "square"), ("fielddominance", "none")):
        sub(sc, k, v)
    audio = sub(media, "audio")
    sub(audio, "numOutputChannels", 2)
    fids = {src: f"file-{i}" for i, src in enumerate(infos, 1)}
    seen = set()

    def clipitem(track, c, n, kind):
        inf = infos[c["src"]]
        ci = sub(track, "clipitem", id=f"clipitem-{n}")
        sub(ci, "name", c["src"].stem)
        sub(ci, "enabled", "TRUE")
        sub(ci, "duration", round(inf["duration"] * float(fps)))
        rate(ci)
        for k, v in (("start", c["at"]), ("end", c["at"] + c["len"]), ("in", c["in"]), ("out", c["in"] + c["len"])):
            sub(ci, k, v)
        f = sub(ci, "file", id=fids[c["src"]])
        if c["src"] not in seen:  # Premiere : le fichier n'est décrit en entier qu'à sa première apparition
            seen.add(c["src"])
            sub(f, "name", c["src"].name)
            sub(f, "pathurl", "file://localhost" + c["src"].as_uri()[len("file://"):])
            sfps = inf.get("fps") or float(fps)
            rate(f, round(sfps), "TRUE" if abs(sfps - round(sfps)) > 0.01 else "FALSE")
            sub(f, "duration", round(inf["duration"] * sfps))
            m = sub(f, "media")
            if "width" in inf:
                vs = sub(sub(m, "video"), "samplecharacteristics")
                sub(vs, "width", inf["width"])
                sub(vs, "height", inf["height"])
            if inf["has_audio"]:
                am = sub(m, "audio")
                asc = sub(am, "samplecharacteristics")
                sub(asc, "depth", 16)
                sub(asc, "samplerate", 48000)
                sub(am, "channelcount", 2)
        if kind == "video":
            # Premiere n'adapte pas un 16:9 au 9:16 : échelle « cover » × zoom
            scale = max(w / inf["width"], h / inf["height"]) * c["zoom"] * 100
            eff = sub(sub(ci, "filter"), "effect")
            for k, v in (("name", "Basic Motion"), ("effectid", "basic"), ("effectcategory", "motion"),
                         ("effecttype", "motion"), ("mediatype", "video")):
                sub(eff, k, v)
            p = sub(eff, "parameter")
            sub(p, "parameterid", "scale")
            sub(p, "name", "Scale")
            sub(p, "value", f"{scale:.2f}")
        else:
            st = sub(ci, "sourcetrack")
            sub(st, "mediatype", "audio")
            sub(st, "trackindex", 1)
            if c.get("gain_db") is not None:
                eff = sub(sub(ci, "filter"), "effect")
                for k, v in (("name", "Audio Levels"), ("effectid", "audiolevels"), ("effectcategory", "audiolevels"),
                             ("effecttype", "audiolevels"), ("mediatype", "audio")):
                    sub(eff, k, v)
                p = sub(eff, "parameter")
                for k, v in (("parameterid", "level"), ("name", "Level"), ("valuemin", 0), ("valuemax", 3.98109),
                             ("value", f"{10 ** (c['gain_db'] / 20):.5f}")):
                    sub(p, k, v)

    n = 1
    for t in (1, 2):
        track = sub(video, "track")
        for c in (c for c in items if c["track"] == t):
            clipitem(track, c, n, "video")
            n += 1
    for t in (1, "musique", "sfx"):  # pistes son 1 voix, 2 musique, 3 bruitages
        track = sub(audio, "track")
        for c in (c for c in items if c["track"] == t and infos[c["src"]]["has_audio"]):
            clipitem(track, c, n, "audio")
            n += 1
    return root


def write_xml(root, path, doctype=""):
    ET.indent(root)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + doctype + ET.tostring(root, encoding="unicode") + "\n",
                    encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Exporte le montage pour Final Cut / DaVinci (.fcpxml), Premiere (.xml) et en .srt.")
    ap.add_argument("edl")
    ap.add_argument("--profil", help="ma-da/profile.json (pour mots_par_bloc des sous-titres)")
    a = ap.parse_args()
    edl_path = Path(a.edl)
    if not edl_path.exists():
        die(f"edl introuvable : {edl_path}")
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    if not edl.get("segments"):
        die("l'edl ne contient aucun segment")
    root = captions.root_of(edl_path)
    prof_path = Path(a.profil) if a.profil else root / "ma-da" / "profile.json"
    profile = json.loads(prof_path.read_text(encoding="utf-8")) if prof_path.exists() else {}
    edl["format"] = fmt = edl.get("format") or profile.get("format") or {"width": 1080, "height": 1920, "fps": 30}
    rendu = root / ".monteur" / "edl.rendu.json"
    if edl_path.resolve() != rendu.resolve():
        if rendu.exists() and rendu.stat().st_mtime >= edl_path.stat().st_mtime:
            edl = json.loads(rendu.read_text(encoding="utf-8"))  # coupes réellement rendues
            edl["format"] = edl.get("format") or fmt
        elif pacing is not None:
            edl = pacing.normalize_edl(edl, root, profile)
            edl["format"] = edl.get("format") or fmt
    fps = Fraction(fmt["fps"]).limit_denominator(1001)
    w, h = fmt["width"], fmt["height"]

    segs = captions.snapped_segments(edl, root)
    items = clips(edl, root, segs, fps, profile)
    infos = {}
    for c in items:
        if c["src"] not in infos:
            infos[c["src"]] = probe(c["src"])
            if "width" not in infos[c["src"]] and c["track"] in (1, 2):
                die(f"pas de piste vidéo dans {c['src']}")

    stem = Path(edl.get("output", "sorties/video.mp4")).stem
    out = root / "sorties"
    out.mkdir(parents=True, exist_ok=True)
    write_xml(fcpxml(items, infos, w, h, fps, stem), out / f"{stem}.fcpxml", "<!DOCTYPE fcpxml>\n")
    write_xml(xmeml(items, infos, w, h, fps, stem), out / f"{stem}.xml", "<!DOCTYPE xmeml>\n")
    per = int({**captions.DEFAULT_ST, **(profile.get("sous_titres") or {})}["mots_par_bloc"])
    (out / f"{stem}.srt").write_text(captions.build_srt(captions.timeline_words(segs, root), per), encoding="utf-8")
    total = sum(s["frames"] for s in segs) / float(fps)
    nmus = sum(1 for c in items if c["track"] == "musique")
    nsfx = sum(1 for c in items if c["track"] == "sfx")
    print(f"Exports : {out / (stem + '.fcpxml')}, {out / (stem + '.xml')}, {out / (stem + '.srt')}\n"
          f"{len(segs)} segments, {len(edl.get('broll') or [])} b-roll, musique {'oui' if nmus else 'non'}, "
          f"{nsfx} bruitages, {sum(1 for c in items if c.get('keys'))} zooms animés, durée {total:.2f} s")


if __name__ == "__main__":
    main()

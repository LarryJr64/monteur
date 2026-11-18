"""Rushs → transcripts JSON (mots horodatés + silences) et .monteur/takes.md."""
import argparse
import difflib
import gc
import json
import os
import re
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import ECONOME, VIDEO_EXT, die, ffmpeg, list_videos, probe, run, whisper_model_path, work_dir  # noqa: E402
from pacing import FILLER_PAUSE, is_filler  # noqa: E402

SILENCE_MIN = 0.3   # trou entre mots compté comme silence
PHRASE_GAP = 0.35   # trou qui coupe une phrase
LOW_CONF = 0.5
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
# Whisper efface les « euh » ; un prompt qui en contient l'incite à les écrire (technique connue)
FILLER_PROMPT = "Euh, alors, hum, bah... euh, voilà."
CACHE_VERSION = 3  # v3 : passages dérivés en anglais repris avec Whisper
PARAKEET = "parakeet-tdt-0.6b-v3"
# langues de Parakeet TDT 0.6B v3 (carte du modèle) ; ailleurs → Whisper
# Parakeet devine la langue seul : sur un accent, il bascule par passages en anglais inventé (mesuré : « the
# dialogue series » pour du français). Trop de mots-outils anglais dans une autre langue → Whisper, langue forcée.
ANGLAIS = set("the and is you that of it's i've they with have been this are can will your what".split())
DERIVE_MAX = 0.03


def derive_anglais(words, langue):
    if langue in ("en", "auto") or not words:
        return 0.0
    mots = norm(words)
    return sum(m in ANGLAIS for m in mots) / max(len(mots), 1)


PARAKEET_LANGS = set("bg hr cs da nl en et fi fr de el hu it lv lt mt pl pt ro sk sl es sv ru uk auto".split())


def load_parakeet():
    """Modèle Parakeet + VAD Silero (onnx-asr), téléchargés une seule fois dans ~/.cache/monteur (dossiers simples :
    pas de liens symboliques, WinError 1314). Sert aussi à doctor.py pour tout pré-télécharger."""
    import onnx_asr
    import onnxruntime as ort
    cache = Path.home() / ".cache" / "monteur"
    so = ort.SessionOptions()
    if ECONOME:  # peu de mémoire libre : pas de réserve mémoire onnxruntime, 2 fils
        so.enable_cpu_mem_arena = False
        so.intra_op_num_threads = 2
    asr = onnx_asr.load_model("nemo-" + PARAKEET, path=cache / PARAKEET, quantization="int8", sess_options=so)
    vad = onnx_asr.load_vad("silero", path=cache / "silero-vad")
    return asr, vad


class Parakeet:
    """NVIDIA Parakeet TDT 0.6B v3 (CC-BY-4.0) via onnx-asr (MIT, onnxruntime, sans PyTorch/NeMo).
    Mesuré sur ce projet (CPU, français) : moins d'erreurs que Whisper large-v3-turbo, bien plus rapide, et il écrit
    les « bah », « hum ». Pas de prompt : le lexique est appliqué après coup (apply_lexique)."""

    def __init__(self):
        asr, vad = load_parakeet()
        # morceaux ≤ 20 s coupés dans les pauses : l'attention reste locale, la mémoire bornée
        self.m = asr.with_vad(vad, max_speech_duration_s=20, min_silence_duration_ms=300,
                              speech_pad_ms=100).with_timestamps()

    def words(self, audio):
        """Jetons horodatés (début, pas de 80 ms) → mots ; fin = dernier jeton + 80 ms, bornée par le mot suivant."""
        out = []
        for seg in self.m.recognize(audio):
            cur = None
            for tok, ts, lp in zip(seg.tokens or [], seg.timestamps or [], seg.logprobs or [0.0] * len(seg.tokens or [])):
                t = seg.start + ts
                if tok.startswith((" ", "▁")) or cur is None:
                    cur = {"w": tok.strip(" ▁"), "start": t, "end": t + 0.08, "lp": [lp]}
                    out.append(cur)
                else:
                    cur["w"] += tok
                    cur["end"] = t + 0.08
                    cur["lp"].append(lp)
        words = []
        for k, w in enumerate(out):
            if not w["w"].strip():
                continue
            nxt = out[k + 1]["start"] if k + 1 < len(out) else w["end"]
            words.append({"w": w["w"], "start": round(w["start"], 2), "end": round(max(w["start"] + 0.04, min(w["end"], nxt)), 2),
                          "p": round(float(np.exp(np.mean(w["lp"]))), 2)})
        return words


def apply_lexique(words, mots):
    """Parakeet n'a pas de prompt : on corrige les mots très proches d'un mot du lexique (« Mattéo » → « Matteo »)."""
    lex = {m.lower(): m for m in mots if m and " " not in m}
    for w in words:
        core = re.sub(r"[^\w'’-]", "", w["w"])
        if len(core) < 4 or core.lower() in lex:
            continue
        hit = difflib.get_close_matches(core.lower(), lex, n=1, cutoff=0.84)
        if hit and hit[0][0] == core[0].lower():
            w["w"] = w["w"].replace(core, lex[hit[0]])
    return words


def collect(inputs):
    files = []
    for i in inputs:
        p = Path(i)
        if p.is_dir():
            files += list_videos(p) + sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXT)
        elif p.is_file():
            files.append(p)
        else:
            die(f"introuvable : {i}")
    if not files:
        die("aucune vidéo trouvée (extensions : " + ", ".join(sorted(VIDEO_EXT)) + ")")
    return files


def rel(path, root):
    try:
        return Path(os.path.relpath(path, root)).as_posix()
    except ValueError:  # autre lecteur Windows
        return Path(path).resolve().as_posix()


def find_silences(words, duration):
    """Trous entre mots > SILENCE_MIN, plus début et fin de fichier."""
    bounds = [0.0] + [x for w in words for x in (w["start"], w["end"])] + [duration]
    out = []
    for a, b in zip(bounds[0::2], bounds[1::2]):
        if b - a > SILENCE_MIN:
            out.append({"start": round(a, 2), "end": round(b, 2)})
    return out


def sentences(words):
    """Groupes de mots coupés sur ponctuation finale ou silence > PHRASE_GAP."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or re.search(r"[.!?…]$", w["w"]) or nxt["start"] - w["end"] > PHRASE_GAP:
            out.append(cur)
            cur = []
    return out


def norm(s):
    return [t for t in re.sub(r"[^\w']+", " ", " ".join(w["w"] for w in s if not w.get("filler")).lower()).split() if t]


def word_text(w):
    """Mot pour takes.md : hésitation entre crochets ([euh]), confiance basse suivie de « ? »."""
    if w.get("filler"):
        return w["w"] if w["w"].startswith("[") else f"[{w['w']}]"
    return w["w"] + ("?" if w["p"] < LOW_CONF else "")


def is_retake(a, b):
    """a puis b : b reprend-il a ? (même début de 3 mots, texte très proche,
    ou même début de 2 mots et début de b proche de a : « La solution c'est… » / « La solution est simple… »)."""
    na, nb = norm(a), norm(b)
    if len(na) >= 3 and na[:3] == nb[:3]:
        return True
    ta, tb = " ".join(na), " ".join(nb)
    if difflib.SequenceMatcher(None, ta, tb).ratio() > 0.7:
        return True
    return len(na) >= 2 and na[:2] == nb[:2] and difflib.SequenceMatcher(None, ta, tb[:len(ta)]).ratio() > 0.7


RETAKE_LOOKAHEAD, RETAKE_WINDOW = 3, 15.0  # une reprise peut venir 1 à 3 phrases plus loin (« Tu bois… ? C'est… » ×3)


def takes_lines(t):
    name = Path(t["source"]).name
    sents = sentences(t["words"])
    lines, retakes = [], 0
    for i, s in enumerate(sents):
        text = " ".join(word_text(w) for w in s)
        text = re.sub(r" (?=['’]|-\w)", "", text)  # Whisper découpe « aujourd 'hui », « abonne -toi »
        # on marque la prise abandonnée : c'est en général la DERNIÈRE qui est bonne
        flag = any(is_retake(s, n) for n in sents[i + 1: i + 1 + RETAKE_LOOKAHEAD]
                   if n[0]["start"] - s[-1]["end"] < RETAKE_WINDOW)
        retakes += flag
        lines.append(f"{'(reprise?) ' if flag else ''}[{name} {s[0]['start']:.2f}-{s[-1]['end']:.2f}] {text}")
    return lines, retakes


def transcribe(model, src, args, prompt):
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "a.wav")
        run([ffmpeg(), "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", wav])
        # on lit le wav nous-mêmes : le décodeur PyAV de faster-whisper casse avec av>=14
        with wave.open(wav) as wf:
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        # pas de BatchedInferencePipeline : avec un lexique (initial_prompt) il saute des phrases entières
        # (mesuré : 13 % d'erreurs contre 4 %), pour un gain de vitesse de 7 à 28 % seulement
        if isinstance(model, Parakeet):
            words, lang = model.words(audio), args.langue
            if prompt:
                apply_lexique(words, prompt.split(", "))
        else:
            fr = args.langue in ("fr", "auto")
            prompts = (FILLER_PROMPT if fr else "", prompt and f"Vocabulaire : {prompt}.")
            segs, info = model.transcribe(
                audio, language=None if args.langue == "auto" else args.langue, word_timestamps=True,
                vad_filter=True, hotwords=prompt, initial_prompt=" ".join(x for x in prompts if x) or None)
            words = [{"w": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2),
                      "p": round(w.probability, 2)} for s in segs for w in (s.words or []) if w.word.strip()]
            lang = info.language
        detected = silencedetect(wav)
    words = casse(glue(words))
    for a, b in zip(words, words[1:]):  # garde-fou : horodatage strictement croissant
        b["start"] = max(b["start"], a["end"])
        b["end"] = max(b["end"], b["start"])
    words = refine_edges(snap_to_silences(words, detected), audio)
    for w in words:
        if is_filler(w):
            w["filler"] = True
    return vad_fillers(words, audio), lang


def frame_db(audio, sr=16000, hop=0.01):
    """Énergie (dBFS) par trame de 10 ms (fenêtre 20 ms)."""
    h = int(sr * hop)
    n = max(1, len(audio) // h)
    x = np.pad(audio[:n * h], (0, h)).reshape(-1, h)
    e = (x[:-1] ** 2).mean(axis=1) / 2 + (x[1:] ** 2).mean(axis=1) / 2
    return 10 * np.log10(e + 1e-10)


def refine_edges(words, audio, sr=16000, hop=0.01, max_end=0.25, max_start=0.12):
    """Fins de mots annoncées trop tôt par Whisper (consonnes finales faibles) : on prolonge la fin (et avance le
    début) tant que l'énergie reste au-dessus du bruit de fond, sans toucher le mot voisin. Ne raccourcit jamais :
    une coupe posée « fin du mot + traîne » ne mange donc pas la fin du mot."""
    if not words or not len(audio):
        return words
    db = frame_db(audio, sr, hop)
    floor, ref = np.percentile(db, 10), np.percentile(db, 95)
    thr = max(floor, -80.0) + 0.25 * (ref - max(floor, -80.0))
    loud = db > thr
    n = len(db)
    for k, w in enumerate(words):
        nxt = words[k + 1]["start"] - 0.02 if k + 1 < len(words) else len(audio) / sr
        f, stop = int(w["end"] / hop), int(min(nxt, w["end"] + max_end) / hop)
        while f < min(stop, n) and loud[f]:
            f += 1
        w["end"] = round(max(w["end"], min(f * hop, nxt)), 2)
        prv = words[k - 1]["end"] + 0.02 if k else 0.0
        f, stop = int(w["start"] / hop) - 1, int(max(prv, w["start"] - max_start) / hop)
        while f >= max(stop, 0) and f < n and loud[f]:
            f -= 1
        w["start"] = round(min(w["start"], max((f + 1) * hop, prv)), 2)
    return words


def vad_fillers(words, audio, sr=16000):
    """Parole détectée (VAD Silero de faster-whisper) sans aucun mot : presque toujours un « euh » effacé par
    Whisper. On l'ajoute comme mot `[euh]` (filler) s'il est isolé par ≥ 100 ms de chaque côté et court (≤ 1 s :
    au-delà c'est plutôt une phrase sautée, qu'on ne veut surtout pas couper)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    chunks = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=100, speech_pad_ms=0,
                                                     min_speech_duration_ms=120), sampling_rate=sr)
    busy = [(w["start"] - FILLER_PAUSE, w["end"] + FILLER_PAUSE) for w in words]
    extra = []
    for c in chunks:
        a, b = c["start"] / sr, c["end"] / sr
        for s, e in busy:  # on retire de la plage de parole tout ce qui est près d'un mot connu
            if s < b and e > a:
                if s <= a:
                    a = max(a, e)
                elif e >= b:
                    b = min(b, s)
                else:  # mot au milieu de la plage : on ne garde que le morceau le plus long
                    a, b = (a, s) if s - a >= b - e else (e, b)
            if b - a <= 0:
                break
        if 0.15 <= b - a <= 1.0:
            extra.append({"w": "[euh]", "start": round(a, 2), "end": round(b, 2), "p": 0.0, "filler": True})
    return sorted(words + extra, key=lambda w: w["start"])


def casse(words):
    """Parakeet met une majuscule après chaque pause (« Plein d'événements », « quand même Très ») : visible dans des
    sous-titres en minuscules. On la retire au milieu d'une phrase si le mot existe aussi en minuscule dans le
    transcript (un nom propre, lui, n'y est jamais : « Maurice », « Nikki » restent)."""
    minus = {w["w"].strip(".,;:!?…") for w in words if w["w"][:1].islower()}
    for a, b in zip(words, words[1:]):
        m = b["w"].strip(".,;:!?…")
        if m[:1].isupper() and not m.isupper() and not a["w"].rstrip().endswith((".", "?", "!", "…"))                 and m.lower() in minus:
            b["w"] = b["w"][0].lower() + b["w"][1:]
    return words


def glue(words):
    """Whisper coupe « C » + « 'est », « abonne » + « -toi », « 100 » + « % » : on recolle en un mot (début du 1er, fin du 2e)."""
    out = []
    for w in words:
        # pas à travers une pause : « j » + 1,6 s + « 'ai » est un « euh » mal entendu suivi de « j'ai »
        # « 100 » + « % » : le symbole seul disparaîtrait des sous-titres
        if out and (re.match(r"['’]|-\w", w["w"]) or re.fullmatch(r"[%€$]+[.,!?]*", w["w"])) \
                and w["start"] - out[-1]["end"] < 0.3:
            prev = out[-1]
            out[-1] = {"w": prev["w"] + w["w"], "start": prev["start"], "end": max(prev["end"], w["end"]),
                       "p": max(prev["p"], w["p"])}  # le fragment seul a souvent une confiance basse
        else:
            out.append(w)
    return out


def silencedetect(wav):
    log = run([ffmpeg(), "-hide_banner", "-i", wav, "-af", f"silencedetect=n=-35dB:d={SILENCE_MIN}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    return list(zip(starts, ends))  # un silence encore ouvert en fin de fichier est ignoré (la fin est déjà comptée)


def snap_to_silences(words, detected):
    """Whisper étire souvent le dernier mot avant une pause : on recale les bords de mots sur les vrais silences."""
    for s, e in detected:
        for w in words:
            if w["start"] < s < w["end"] <= e + 0.05:
                w["end"] = round(max(s, w["start"] + 0.05), 2)
            elif s - 0.05 <= w["start"] < e < w["end"]:
                w["start"] = round(min(e, w["end"] - 0.05), 2)
    return words


def dump(t):
    """JSON lisible par Claude avec Read : un mot / un silence par ligne (indent=1 en ferait 6)."""
    head = json.dumps({k: v for k, v in t.items() if k not in ("words", "silences")}, ensure_ascii=False)[:-1]
    lists = "".join(f', "{k}": [\n  ' + ",\n  ".join(json.dumps(x, ensure_ascii=False) for x in t[k]) + "\n ]"
                    for k in ("words", "silences"))
    return head + lists + "}\n"


def transcript_name(src, root, tdir, dup):
    """<stem>, ou <dossier>_<stem> si un autre fichier du même nom a son transcript (ex. mes-videos/ et rushs/)."""
    out = tdir / f"{src.stem}.json"
    other = out.exists() and json.loads(out.read_text(encoding="utf-8"))["source"] != rel(src, root)
    return f"{src.parent.name}_{src.stem}" if dup or other else src.stem


def main():
    ap = argparse.ArgumentParser(description="Transcrit les rushs (local, gratuit) avec mots horodatés.")
    ap.add_argument("inputs", nargs="+", help="dossier(s) ou fichier(s)")
    ap.add_argument("--model", default=PARAKEET,
                    help=f"{PARAKEET} (défaut : le plus juste et le plus rapide en français) | "
                         "large-v3-turbo | small | tiny (Whisper)")
    ap.add_argument("--rapide", action="store_true",
                    help="modèle small : ~3x plus rapide, un peu plus de fautes dans les sous-titres")
    ap.add_argument("--langue", default="fr", help="code langue ou 'auto'")
    ap.add_argument("--lexique", help="noms propres/mots du créateur, un par ligne (défaut : ma-da/lexique.txt s'il existe)")
    ap.add_argument("--takes", help="fichier des phrases (défaut : .monteur/takes.md)")
    ap.add_argument("--root", default=".", help="dossier de travail (contient .monteur/)")
    args = ap.parse_args()
    if args.rapide:
        args.model = "small"
    if args.model == PARAKEET and args.langue not in PARAKEET_LANGS:
        print(f"langue « {args.langue} » non gérée par Parakeet : Whisper large-v3-turbo")
        args.model = "large-v3-turbo"

    files = collect(args.inputs)
    tdir = work_dir(args.root) / "transcripts"
    prompt = None
    if not args.lexique and (Path(args.root) / "ma-da" / "lexique.txt").is_file():
        args.lexique = str(Path(args.root) / "ma-da" / "lexique.txt")
    if args.lexique:
        if not Path(args.lexique).is_file():
            die(f"lexique introuvable : {args.lexique}")
        mots = [l.strip() for l in Path(args.lexique).read_text(encoding="utf-8-sig").splitlines()]
        prompt = ", ".join(m for m in mots if m and not m.startswith("#")) or None

    model = secours = None
    transcripts = []
    stems = [f.stem for f in files]
    for src in files:
        name = transcript_name(src, args.root, tdir, stems.count(src.stem) > 1)
        out, cache = tdir / f"{name}.json", tdir / f"{name}.cache"
        st = src.stat()
        key = {"mtime": st.st_mtime, "size": st.st_size, "model": args.model, "lexique": prompt, "v": CACHE_VERSION}
        old = json.loads(cache.read_text(encoding="utf-8")) if out.exists() and cache.exists() else None
        if old == key:
            print(f"{src.name} : déjà transcrit (cache)")
            transcripts.append(json.loads(out.read_text(encoding="utf-8")))
            continue
        if old and args.model == PARAKEET and {**old, "lexique": prompt} == key and prompt:
            # Parakeet applique le lexique après coup : nouveau lexique = mêmes mots corrigés, sans recharger le modèle (1 Go)
            t = json.loads(out.read_text(encoding="utf-8"))
            apply_lexique(t["words"], prompt.split(", "))
            out.write_text(dump(t), encoding="utf-8")
            cache.write_text(json.dumps(key), encoding="utf-8")
            print(f"{src.name} : lexique appliqué au transcript existant (sans retranscrire)")
            transcripts.append(t)
            continue
        info = probe(src)
        if not info["has_audio"]:
            print(f"{src.name} : pas de piste audio, ignoré")
            continue
        if model is None and args.model == PARAKEET:
            secours = None  # libère Whisper avant de recharger Parakeet
            gc.collect()
            print(f"chargement du modèle {PARAKEET}…", flush=True)
            try:
                model = Parakeet()
            except Exception as e:  # onnx-asr absent, réseau, disque : Whisper fait le travail
                print(f"  Parakeet indisponible ({e}) : repli sur Whisper large-v3-turbo", flush=True)
                args.model = "large-v3-turbo"
                key["model"] = args.model
        if model is None:
            from faster_whisper import WhisperModel
            print(f"chargement du modèle {args.model}…", flush=True)
            try:
                path = whisper_model_path(args.model)
            except Exception as e:  # réseau, disque, nom de modèle inconnu
                die(f"modèle Whisper {args.model} indisponible : {e}. Lance : python scripts/doctor.py")
            model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
        print(f"{src.name} ({info['duration']:.1f} s) : transcription…", flush=True)
        t0 = time.time()
        words, lang = transcribe(model, src, args, prompt)
        modele = args.model
        d = derive_anglais(words, args.langue) if isinstance(model, Parakeet) else 0.0
        if d > DERIVE_MAX:
            print(f"  {round(100 * d)} % de mots anglais : la transcription a dérivé, reprise avec Whisper "
                  f"(langue {args.langue}, ~3x plus lent)", flush=True)
            if secours is None:
                from faster_whisper import WhisperModel
                model = None  # jamais deux modèles en mémoire (≈ 2-3 Go chacun) ; Parakeet rechargé au fichier suivant
                gc.collect()
                secours = WhisperModel(whisper_model_path("large-v3-turbo"), device="cpu", compute_type="int8",
                                       cpu_threads=os.cpu_count() or 4)
            words, lang = transcribe(secours, src, args, prompt)
            modele = "large-v3-turbo"
        el = time.time() - t0
        print(f"  {len(words)} mots en {el:.1f} s (x{info['duration'] / max(el, 0.01):.1f} temps réel)", flush=True)
        t = {"source": rel(src, args.root), "duration": round(info["duration"], 2), "language": lang,
             "model": modele, "words": words, "silences": find_silences(words, info["duration"])}
        out.write_text(dump(t), encoding="utf-8")
        cache.write_text(json.dumps(key), encoding="utf-8")
        transcripts.append(t)

    lines, total_retakes = [], 0
    for t in transcripts:
        l, r = takes_lines(t)
        lines += l
        total_retakes += r
    takes = Path(args.takes) if args.takes else tdir.parent / "takes.md"
    takes.parent.mkdir(parents=True, exist_ok=True)
    takes.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\nRésumé :")
    for t in transcripts:
        print(f"  {Path(t['source']).name} : {t['duration']:.1f} s, {len(t['words'])} mots, "
              f"{len(t['silences'])} silences")
    print(f"  {len(transcripts)} fichier(s), {sum(t['duration'] for t in transcripts):.1f} s, "
          f"{sum(len(t['words']) for t in transcripts)} mots, {total_retakes} reprise(s) probable(s)")
    print(f"  → {takes}")


if __name__ == "__main__":
    main()

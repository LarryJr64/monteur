"""Son (lot A) : nettoyage de la voix, musique duckée, bruitages, loudness finale.

Appelé par render.py (contrat docs/design.md, Version 2) :
  prepare_voice(wav_in, wav_out, mode="auto", profile=None) -> dict
  finish_mix(voice_wav, edl_rendu, profile, root, wav_out) -> dict
  detect_sfx(audio_path, words) -> list[{"at", "type_guess", "force_db"}]   (pour analyze_style)

CLI : python scripts/audio.py nettoyer in.wav out.wav [--mode auto]
      python scripts/audio.py analyser in.wav
      python scripts/audio.py generer-sfx        (recrée assets/sfx/*.wav, sons synthétisés, sans licence)

Débruitage : RNNoise (ffmpeg arnndn, modèle Xiph d'origine std.rnnn embarqué) mélangé à la voix d'origine ;
`fort` utilise ClearerVoice MossFormer2 ou DeepFilterNet3 s'ils sont installés (PyTorch, lent), sinon RNNoise à 95 %.
"""
import argparse
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import statistics
import wave
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import MUSIQUE_DB_DEFAUT, die, ffmpeg  # noqa: E402

SR = 48000
HOP = 0.01  # trame d'analyse : 10 ms
PLUGIN = Path(__file__).resolve().parent.parent
SFX_DIR = PLUGIN / "assets" / "sfx"
RNNN = PLUGIN / "assets" / "rnnoise" / "std.rnnn"
MODES = ("auto", "leger", "fort", "aucun")
SFX_TYPES = ("whoosh", "pop", "impact", "ding", "click", "riser")


# ---------- E/S ----------

def _ff(args, data=None, cwd=None):
    """ffmpeg avec entrée binaire optionnelle sur stdin ; renvoie (stdout bytes, stderr texte)."""
    r = subprocess.run([ffmpeg(), "-hide_banner", "-y", *args], input=data, capture_output=True, cwd=cwd,
                       stdin=None if data is not None else subprocess.DEVNULL)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        die(f"commande échouée (ffmpeg) :\n{err[-1500:]}")
    return r.stdout, err


def load(path, sr=SR, ch=1):
    """N'importe quel média → float32 (n,) si ch=1, (n, ch) sinon."""
    if not Path(path).exists():
        die(f"fichier audio introuvable : {path}")
    out, _ = _ff(["-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", str(ch), "-ar", str(sr), "-"])
    x = np.frombuffer(out, dtype=np.float32)
    return x if ch == 1 else x.reshape(-1, ch)


def save(path, x, sr=SR):
    """float → wav 16 bits (mono ou stéréo selon la forme)."""
    x = np.nan_to_num(np.asarray(x, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)  # filet : jamais de NaN écrit
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1 if x.ndim == 1 else x.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def _raw_in(x, sr=SR):
    ch = 1 if x.ndim == 1 else x.shape[1]
    return ["-f", "f32le", "-ar", str(sr), "-ac", str(ch), "-i", "-"], np.ascontiguousarray(x, np.float32).tobytes()


def loudness(x, sr=SR):
    """Mesure EBU R128 d'un tableau (filtre ebur128, true peak suréchantillonné) : input_i, input_tp, input_lra."""
    inp, data = _raw_in(x, sr)
    _, err = _ff(["-v", "info", *inp, "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"], data)
    summary = err[err.rindex("Summary:"):]
    val = lambda key: float(re.search(key + r":\s+(-?[\d.]+|-inf)", summary).group(1))  # noqa: E731
    return {"input_i": val("I"), "input_tp": val("Peak"), "input_lra": val("LRA")}


def _frames_db(x, sr):
    """Niveau RMS (dB FS) par trame de 10 ms, fenêtre 20 ms."""
    h = int(sr * HOP)
    n = len(x) // h
    if n == 0:
        return np.full(1, -120.0)
    e = (x[:n * h].reshape(n, h).astype(np.float64) ** 2).mean(1)
    e = np.convolve(e, [0.5, 0.5], "same")
    return 10 * np.log10(e + 1e-12)


# ---------- analyse ----------

def analyze(path):
    """Plancher de bruit, niveau de parole, SNR, ronflette secteur, réverbération grossière."""
    sr = 16000
    x = load(path, sr)
    db = _frames_db(x, sr)
    noise = float(max(np.percentile(db, 10), -100))
    speech = float(np.percentile(db, 90))
    res = {"duree_s": round(len(x) / sr, 2), "bruit_db": round(noise, 1), "parole_db": round(speech, 1),
           "snr_db": round(speech - noise, 1), "ronflette_hz": None, "rt60_s": None}
    if len(x) >= 2 * sr:
        # spectre long terme (résolution 1 Hz) : une ronflette secteur reste une raie fine, la voix s'étale
        seg = x[:len(x) // sr * sr].reshape(-1, sr) * np.hanning(sr)
        p = 10 * np.log10((np.abs(np.fft.rfft(seg, axis=1)) ** 2).mean(0) + 1e-20)
        for f0 in (50, 60):
            hits = sum(p[k] - np.median(np.r_[p[k - 12:k - 3], p[k + 4:k + 13]]) > 10 for k in (f0, 2 * f0, 3 * f0))
            if hits >= 2:
                res["ronflette_hz"] = f0
                break
    # réverb : pente de décroissance après les fins de syllabes fortes (×3 sur 20 dB ≈ RT60) ; heuristique
    rts = []
    for i in np.flatnonzero((db[:-1] > speech - 6) & (db[1:] <= speech - 6)):
        tail = db[i:i + 60]
        if len(tail) < 60 or tail[5:].max() > tail[0]:
            continue
        drop = min(20.0, tail[0] - noise - 6)
        j = np.argmax(tail < tail[0] - drop)
        if drop >= 10 and j:
            rts.append(60 * j * HOP / drop)
    if len(rts) >= 3:
        res["rt60_s"] = round(float(np.median(rts)), 2)
    return res


# ---------- nettoyage de la voix ----------

def _enhance_ml(x):
    """Débruiteur neuronal optionnel pour `fort` : (signal, nom) ou None s'il n'est pas installé.
    1. ClearerVoice MossFormer2_SE_48K (Apache-2.0) : meilleur au banc (DNSMOS = voix propre) mais ~0,05x temps réel.
    2. DeepFilterNet3 (MIT/Apache-2.0) : pas de roue Python 3.12+ sous Windows (deepfilterlib à compiler en Rust)."""
    try:
        from clearvoice import ClearVoice
    except Exception:
        ClearVoice = None
    if ClearVoice:
        cache, here = Path.home() / ".cache" / "monteur" / "clearvoice", Path.cwd()
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as d:
            save(Path(d) / "in.wav", x)
            os.chdir(cache)  # ClearVoice télécharge ses poids (~210 Mo) dans ./checkpoints
            try:
                cv = ClearVoice(task="speech_enhancement", model_names=["MossFormer2_SE_48K"])
                cv.write(cv(input_path=str(Path(d) / "in.wav"), online_write=False), output_path=str(Path(d) / "out.wav"))
            finally:
                os.chdir(here)
            return load(Path(d) / "out.wav")[:len(x)], "mossformer2_se_48k"
    try:
        import torch
        from df.enhance import enhance, init_df
    except Exception:
        return None
    model, state, _ = init_df(log_level="ERROR")
    return enhance(model, state, torch.from_numpy(x[None].copy()))[0].numpy()[:len(x)], "deepfilternet3"


def _choose(m, mode, profile):
    a = (profile or {}).get("audio") or {}
    if mode != "auto":
        return mode
    # la DA garde une voix brute : on ne touche qu'aux prises vraiment sales
    seuil = 25 if a.get("voix_nettoyee") is False else 38
    if m["parole_db"] < -60 or (m["snr_db"] >= seuil and not m["ronflette_hz"]):  # muet ou déjà propre
        return "aucun"
    return "leger" if m["snr_db"] >= 18 else "fort"


def prepare_voice(wav_in, wav_out, mode="auto", profile=None):
    """Nettoie la voix (wav mono 48 kHz en sortie). mode ∈ auto|leger|fort|aucun.
    auto ne traite pas une prise déjà propre (sur-traiter dégrade la voix).
    Renvoie {"applique", "mode", "methode", "bruit_db", "snr_db", "bruit_db_apres", …}."""
    mode = {False: "aucun", True: "auto", None: "auto"}.get(mode, mode)  # edl écrit à la main : true/false
    if mode not in MODES:
        die(f"mode de nettoyage inconnu : {mode} (attendu : {', '.join(MODES)})")
    m = analyze(wav_in)
    mode = _choose(m, mode, profile)
    res = {"applique": mode != "aucun", "mode": mode, "methode": None, **m}
    x = load(wav_in)
    if mode == "aucun":
        save(wav_out, x)
        return res

    # 1. coupe-bas + débruitage (mélange sec/traité : 100 % sonne artificiel)
    pre = "highpass=f=80:poles=2"
    if m["ronflette_hz"]:
        pre += "".join(f",bandreject=f={m['ronflette_hz'] * k}:width_type=h:w=3" for k in (1, 2, 3, 4))
    inp, data = _raw_in(x)
    y = None
    if mode == "fort":
        dry = load_after(x, pre)
        ml = _enhance_ml(dry)
        if ml is not None:
            y, res["methode"] = ml
            y = 0.85 * y + 0.15 * dry[:len(y)]
            inp, data = _raw_in(y.astype(np.float32))
            pre = "anull"
    if res["methode"] is None:
        res["methode"] = "rnnoise"
        # passe à part : arnndn (ffmpeg 9) vide sa dernière trame (10 ms) avec de la mémoire non initialisée —
        # NaN ou valeurs ~1e3, au hasard d'un lancement à l'autre. Dans la même chaîne, ça contamine EQ et
        # compresseur, et la normalisation crête écrase tout → piste muette. On remplace ces trous par le sec.
        # Il plante aussi (0xC0000005, ~1 fois sur 6) sauf si la longueur est un multiple de sa trame (480) :
        # on complète par du silence aligné (0 plantage, 0 valeur aberrante sur 96 essais), puis on recoupe.
        dry = load_after(x, pre)
        pad = np.concatenate([dry, np.zeros((-len(dry)) % 480 + 4800, np.float32)])
        af = f"arnndn=m={RNNN.name}:mix={0.95 if mode == 'fort' else 0.85}"
        wet = None
        for _ in range(3):  # filet si un plantage survit malgré l'alignement
            try:
                # chemin relatif (cwd = dossier du modèle) : évite l'échappement de « C:\ » dans les filtres
                wet = load_after(pad, af, cwd=RNNN.parent)
                break
            except SystemExit:
                continue
        if wet is None:
            wet = dry
            res["methode"] = "rnnoise en échec → filtres seuls"
        n = len(dry)
        wet = np.pad(wet[:n], (0, max(0, n - len(wet))))
        d = dry
        ok = np.isfinite(wet) & (np.abs(wet) <= 2 * np.abs(d).max() + 0.01)
        wet = np.where(ok, wet, d).astype(np.float32)
        inp, data = _raw_in(wet)
        pre = "anull"

    # 2. EQ douce, compression 2:1 calée sur le niveau de parole, de-esser ; loudness finale dans finish_mix
    thr = 10 ** (m["parole_db"] / 20)  # 2:1 10/250 ms : le moins pénalisé au DNSMOS
    chain = (f"{pre},equalizer=f=280:t=q:w=1:g=-2,equalizer=f=3000:t=q:w=1:g=2,"
             f"acompressor=threshold={min(max(thr, 0.001), 1):.5f}:ratio=2:attack=10:release=250:knee=6,"
             "deesser=i=0.4:m=0.5:f=0.5")
    out, _ = _ff(["-v", "error", *inp, "-af", chain, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"], data,
                 cwd=RNNN.parent)
    y = np.nan_to_num(np.frombuffer(out, np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if np.abs(y).max() < 1e-4 <= np.abs(x).max() * 0.1:  # filet : jamais une voix muette à la place d'une vraie
        save(wav_out, x)
        res.update(applique=False, methode=f"{res['methode']} en échec → voix d'origine")
        return res
    # même niveau de parole qu'en entrée (la compression l'a baissé) : render garde ses repères de niveau
    y = y * min(10 ** ((np.percentile(_frames_db(x, SR), 90) - np.percentile(_frames_db(y, SR), 90)) / 20),
                0.99 / (np.abs(y).max() + 1e-9))
    save(wav_out, y)
    res["bruit_db_apres"] = analyze(wav_out)["bruit_db"]
    return res


def load_after(x, af, cwd=None):
    inp, data = _raw_in(x)
    return np.frombuffer(_ff(["-v", "error", *inp, "-af", af, "-f", "f32le", "-"], data, cwd=cwd)[0], np.float32)


# ---------- mix final ----------

def speech_mask(voice, sr=SR):
    """Parole par trame de 10 ms (énergie) ; respirations et pauses < 0,6 s comptées comme parole (pas de pompage)."""
    db = _frames_db(voice, sr)
    noise, loud = np.percentile(db, 10), np.percentile(db, 95)
    on = db > max(loud - 30, noise + 10)
    return _close(_close(on, 0.08, True), 0.6, False)


def _close(mask, dur, value):
    """Supprime les plages de `value` plus courtes que `dur` (secondes)."""
    m, n, i = mask.copy(), int(dur / HOP), 0
    while i < len(m):
        if m[i] == value:
            j = i
            while j < len(m) and m[j] == value:
                j += 1
            if j - i < n and i > 0 and j < len(m):
                m[i:j] = not value
            i = j
        else:
            i += 1
    return m


def beats(path, duree, debut=0.0, sr=11025):
    """Temps des temps forts (s, depuis `debut` dans le morceau) : flux spectral → tempo par autocorrélation
    (70-180 BPM) → grille calée sur la phase la plus forte. [] si le morceau n'a pas de pulsation nette."""
    out, _ = _ff(["-v", "error", "-ss", f"{debut:.3f}", "-t", f"{duree:.3f}", "-i", str(path), "-vn",
                  "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"])
    x = np.frombuffer(out, np.float32)
    hop, n = 128, 1024
    if len(x) < sr * 2:
        return []
    fr = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    mag = np.log1p(np.abs(np.fft.rfft(fr, axis=1)))
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(1)
    flux = flux - np.convolve(flux, np.ones(16) / 16, "same")  # retire la tendance (montées de volume)
    flux = np.maximum(flux, 0)
    fps = sr / hop
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    if hi >= len(ac) or ac[0] <= 0:
        return []
    lag = lo + int(np.argmax(ac[lo:hi]))
    if ac[lag] < 0.1 * ac[0]:  # pas de pulsation (nappe, piano libre) : on ne cale rien
        return []
    phase = max(range(lag), key=lambda ph: flux[ph::lag].sum())
    # suivi : chaque temps recalé sur le pic réel autour de la prédiction (le tempo entier en trames dériverait)
    out, i, r = [], phase, max(1, lag // 4)
    while i < len(flux):
        a = max(0, i - r)
        j = a + int(np.argmax(flux[a:i + r + 1]))
        out.append(j)
        i = j + lag
    # flux[j] monte quand l'attaque entre dans la fenêtre j+1 (qui finit à (j+1)·hop + n) : instant ≈ j·hop + n
    return [round((j * hop + n) / sr, 3) for j in out]


def cale_sur_temps(t, temps, avant=0.15, apres=0.05):
    """Instant `t` ramené sur le temps fort le plus proche dans [t-avant, t+apres] (jamais en retard sur le mot)."""
    proches = [b for b in temps if t - avant <= b <= t + apres]
    return min(proches, key=lambda b: abs(b - t)) if proches else t


def duck_envelope(mask, duck_db, lift_db=8.0, attack=0.15, release=0.4):
    """Gain musique (dB) par trame : `duck_db` pendant la parole, +lift_db dans les pauses.
    Rampe linéaire en dB : descente en `attack` (anticipée, finie au premier mot), remontée en `release`."""
    target = np.where(mask, duck_db, duck_db + lift_db).astype(float)
    ahead = int(attack / HOP)
    for i in np.flatnonzero(np.diff(mask.astype(int)) == 1) + 1:  # anticipe chaque prise de parole
        target[max(0, i - ahead):i] = duck_db
    g = np.empty_like(target)
    cur, down, up = target[0], lift_db / max(1, ahead), lift_db / max(1, release / HOP)
    for i, t in enumerate(target):
        cur = max(t, cur - down) if t < cur else min(t, cur + up)
        g[i] = cur
    return g


def _fade(x, sr, fin, fout):
    n = len(x)
    env = np.ones(n, np.float32)
    a, b = min(n, int(fin * sr)), min(n, int(fout * sr))
    if a:
        env[:a] = np.linspace(0, 1, a)
    if b:
        env[n - b:] *= np.linspace(1, 0, b)
    return x * env[:, None] if x.ndim == 2 else x * env


def _active_rms_db(x):
    db = _frames_db(x if x.ndim == 1 else x.mean(1), SR)
    return float(10 * np.log10(np.mean(10 ** (db[db > db.max() - 20] / 10))))


def finish_mix(voice_wav, edl_rendu, profile, root, wav_out):
    """Voix montée + musique (bouclée, duckée) + bruitages → wav stéréo 48 kHz normalisé
    (`profile.audio.lufs_cible`, défaut -14 LUFS, true peak ≤ -1 dBTP). edl_rendu : dict ou chemin d'edl.rendu.json.
    Musique : edl.music.volume_db, sinon audio.musique_db_sous_voix, sinon musique_lufs - lufs_cible (défaut
    MUSIQUE_DB_DEFAUT sous la voix), duckée d'après l'énergie de la voix. Bruitages : edl.sfx[].type → assets/sfx/<type>.wav,
    point fort (ancre) calé 1 image avant `at`.
    Renvoie {"lufs", "true_peak_db", "lra", "musique", "musique_db_sous_voix", "sfx_places", "sfx_manquants", …}."""
    if not isinstance(edl_rendu, dict):
        edl_rendu = json.loads(Path(edl_rendu).read_text(encoding="utf-8"))
    a = (profile or {}).get("audio") or {}
    target = float(a.get("lufs_cible", -14))
    root = Path(root)
    voice = load(voice_wav, SR, 2)
    n = len(voice)
    mono = voice.mean(1)
    mask = speech_mask(mono)
    res = {"parole_s": round(float(mask.sum()) * HOP, 1), "musique": False, "sfx_places": [], "sfx_manquants": []}
    # niveau de voix de référence ; voix muette (pas de parole) : -20 dB FS par défaut, sinon musique à -138 dB
    lv = _active_rms_db(mono) if mask.any() else -20.0
    mix = voice.copy()

    music = edl_rendu.get("music") or {}
    if music.get("source") and n:
        src = root / music["source"]
        if not src.exists():
            die(f"musique introuvable : {src}")
        out, _ = _ff(["-v", "error", "-stream_loop", "-1", "-ss", f"{float(music.get('debut_s', 0)):.3f}",
                      "-i", str(src), "-vn", "-t", f"{n / SR:.6f}",
                      "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"])
        mus = np.frombuffer(out, np.float32).reshape(-1, 2)[:n]
        mus = np.pad(mus, ((0, n - len(mus)), (0, 0)))
        if "volume_db" in music:
            rel = music["volume_db"]
        elif a.get("musique_db_sous_voix") is not None:
            rel = a["musique_db_sous_voix"]
        elif a.get("musique") and a.get("musique_lufs") is not None:
            rel = a["musique_lufs"] - target
        else:
            rel = MUSIQUE_DB_DEFAUT
        rel = float(min(max(rel, -30), -6))
        mus_db = _active_rms_db(mus)
        if mus_db > -80:
            lift = 8.0  # remontée dans les pauses ≥ 0,6 s et à la fin (sans parole : tout au niveau pause)
            g = duck_envelope(mask, lv + rel - mus_db, lift)
            gain = 10 ** (np.interp(np.arange(n) / SR, np.arange(len(g)) * HOP, g) / 20)
            # pleine (niveau pause) après le dernier mot, fondu court sur la toute fin
            mix += _fade(mus, SR, 0.05, min(0.4, n / SR / 4)) * gain[:, None].astype(np.float32)
            res.update(musique=True, musique_db_sous_voix=rel, remontee_pauses_db=lift)

    fps = (edl_rendu.get("format") or {}).get("fps", 30)
    for s in edl_rendu.get("sfx") or []:
        f = SFX_DIR / f"{s.get('type', '')}.wav"
        if not f.exists():
            res["sfx_manquants"].append(s.get("type"))
            continue
        fx = load(f, SR, 2)
        if s.get("duree_s"):  # son écourté (coup du carton avant le 1er tic) : fondu de sortie court
            d = float(s["duree_s"])
            fx = _fade(fx[:int(d * SR)], SR, 0.0, min(0.05, d / 2))
        start = int(round(depart(f, float(s["at"]), fps) * SR))
        fx = fx[max(0, -start):]
        start = max(0, start)
        fx = fx[:max(0, n - start)]
        if not len(fx):
            continue
        fx = fx * 10 ** ((lv + float(s.get("volume_db", -14)) - _active_rms_db(fx)) / 20)
        mix[start:start + len(fx)] += fx.astype(np.float32)
        res["sfx_places"].append({"type": s["type"], "debut_s": round(start / SR, 3)})

    # loudness finale : gain linéaire vers la cible, limiteur crête (-2 dBFS ≈ -1,5 dBTP), puis
    # retouche de gain mesurée. loudnorm seul repasse en mode dynamique dès que la crête déborde (±2 LU).
    y = np.nan_to_num(mix, nan=0.0, posinf=0.0, neginf=0.0)
    for _ in range(4):  # 2 passes en général ; plus si le limiteur mord beaucoup (voix très crêteuse)
        i = loudness(y)["input_i"]
        if i <= -70 or abs(i - target) < 0.3:
            break  # silence (rien à normaliser) ou cible atteinte
        if not np.isfinite(i):
            break
        y = y * np.float32(10 ** ((target - i) / 20))
        if np.abs(y).max() > 10 ** (-2.5 / 20):
            inp, data = _raw_in(y)
            out, _ = _ff(["-v", "error", *inp, "-af", "alimiter=limit=0.794:attack=2:release=60:level=false",
                          "-f", "f32le", "-"], data)
            y = np.frombuffer(out, np.float32).reshape(-1, 2)[:n]
    save(wav_out, y)
    f = loudness(y)
    res.update(lufs=round(float(f["input_i"]), 2), true_peak_db=round(float(f["input_tp"]), 2),
               lra=round(float(f["input_lra"]), 1), lufs_cible=target)
    return res


@functools.lru_cache(None)
def ancre(path):
    """Instant (s) du point fort d'un bruitage dans son fichier, d'après son nom (= son type) : crête RMS pour un
    souffle (whoosh, swoosh), FIN pour un riser (il monte vers la coupe), attaque pour le reste (impact, pop, click…)."""
    nom = Path(path).stem.lower()
    x = load(path)
    if "riser" in nom:
        return len(x) / SR
    db = _frames_db(x, SR)
    if "whoosh" in nom or "swoosh" in nom:
        return float(np.argmax(db)) * HOP
    return float(np.argmax(db > db.max() - 12)) * HOP


def depart(path, at, fps):
    """Début du fichier pour que son point fort tombe 1 image avant `at` (l'œil voit la coupe, l'oreille l'a déjà)."""
    return at - 1 / fps - ancre(str(path))


FAMILLES = {"whoosh": ("whoosh", "whoosh_2", "whoosh_3", "swoosh"), "pop": ("pop", "pop_2")}
NIVEAUX_CARTON = {"impact": -8.0, "tic": -20.0, "riser": -14.0}  # dB sous la voix si la référence ne dit rien


def _duree(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def carton_sfx(carton, appris, fps=30):
    """Bruitages d'un carton d'ouverture (b-roll `carton: true`) d'après `audio.sfx_carton` du profil (appris sur la
    référence) : impact sur l'entrée, un tic à chaque temps (intervalle appris), riser qui finit sur la coupe de sortie."""
    a = float(carton["at"])
    fin = a + float(carton["duration"])
    niv = {**NIVEAUX_CARTON, **(appris.get("niveaux_db") or {})}
    out = []
    if appris.get("impact"):
        imp = {"type": "impact", "at": a, "volume_db": niv["impact"]}
        if appris.get("premier_tic_s"):  # le coup du pro s'éteint avant le 1er tic ; une traîne de 1 s les masquerait
            imp["duree_s"] = round(float(appris["premier_tic_s"]), 3)
        out.append(imp)
    iv = appris.get("intervalle_s")
    if iv and iv >= 2 / fps:
        # le fichier de la famille dont la durée colle à l'intervalle (un whoosh de 0,7 s tous les 0,27 s = bouillie)
        noms = [n for n in FAMILLES.get(appris.get("tic") or "whoosh", (appris.get("tic"),)) if (SFX_DIR / f"{n}.wav").exists()]
        tic = min(noms, key=lambda n: abs(_duree(SFX_DIR / f"{n}.wav") - iv)) if noms else "swoosh"
        t = a + float(appris.get("premier_tic_s") or iv)
        while t <= fin - iv / 2:
            out.append({"type": tic, "at": round(t, 3), "volume_db": niv["tic"]})
            t += iv
    if appris.get("riser"):
        out.append({"type": "riser", "at": round(fin, 3), "volume_db": niv["riser"]})
    return [dict(x, carton=True) for x in out]


def carton_appris(path, found, fin):
    """Ce que la référence fait sonner sur son carton muet [0, fin] (détections HORS parole, donc fiables) : temps,
    impact d'entrée, tics réguliers (intervalle médian, type deviné), riser final, niveaux (dB par rapport à la voix,
    comme volume_db). found = [{"t", "type"}] du carton. None s'il n'y a presque rien."""
    found = sorted(found, key=lambda f: f["t"])
    if len(found) < 2:
        return None
    x = load(path)
    voix = _active_rms_db(x[int(fin * SR):] if len(x) > (fin + 1) * SR else x)

    def niveau(a, b):
        return round(_active_rms_db(x[int(a * SR):max(int(b * SR), int(a * SR) + 480)]) - voix, 1)

    impact = found[0]["t"] < 0.1
    reste = found[1:] if impact else found
    final = [f for f in reste if f["type"] == "riser" and f["t"] > fin * 2 / 3]
    tics = [f for f in reste if f not in final]
    ts = [f["t"] for f in reste]
    iv = round(statistics.median(b - a for a, b in zip(ts, ts[1:])), 3) if len(ts) >= 2 else None
    types = Counter(f["type"] for f in tics if f["type"] != "riser")
    niv = {}
    if impact:
        niv["impact"] = niveau(0, min(0.25, ts[0] if ts else 0.25))
    if tics and iv:
        niv["tic"] = round(statistics.median(niveau(f["t"], f["t"] + min(iv, 0.25)) for f in tics), 1)
    if final:
        niv["riser"] = niveau(final[0]["t"], fin)
    return {"duree_s": round(fin, 3), "t": [f["t"] for f in found], "impact": impact, "intervalle_s": iv,
            "premier_tic_s": ts[0] if ts else None, "tic": types.most_common(1)[0][0] if types else "whoosh",
            "riser": bool(final), "types": dict(Counter(f["type"] for f in found)), "niveaux_db": niv}


# ---------- repérage des bruitages dans les références ----------

def detect_sfx(audio_path, words, min_gap=0.25):
    """HEURISTIQUE : transitoires hors des mots (onsets par flux spectral), devinés par la forme du spectre.
    words = [{"start", "end"}…] (temps du fichier) ; vide → parole repérée par voisement. Renvoie [{"at", "type_guess", "force_db"}].
    Limites : une musique très rythmée en pause donne des faux positifs ; un bruitage sous la voix est ignoré."""
    sr, nfft, hop = 22050, 1024, 256
    x = load(audio_path, sr)
    if len(x) < nfft * 4:
        return []
    fr = np.lib.stride_tricks.sliding_window_view(x, nfft)[::hop] * np.hanning(nfft)
    S = np.abs(np.fft.rfft(fr, axis=1))
    L = np.log1p(S * 100)
    flux = np.maximum(np.diff(L, axis=0), 0).sum(1)
    flux = np.r_[L[0].sum(), flux]  # un coup posé sur la toute 1re image compte (attaque depuis le silence)
    thr = np.median(flux) + 6 * np.median(np.abs(flux - np.median(flux))) + 1e-9
    t = np.arange(len(flux)) * hop / sr
    e_db = 10 * np.log10((S ** 2).sum(1) + 1e-12)
    busy = np.zeros(len(t), bool)
    for w in words or []:
        busy |= (t >= w["start"] - 0.05) & (t <= w["end"] + 0.05)
    if not words:
        # sans transcript : trames voisées (autocorrélation forte à 80-400 Hz) = parole, élargies de 0,15 s.
        # Les bruitages (souffle, clic, pop court, cloche aiguë) ne sont pas voisés dans cette plage.
        ac = np.fft.irfft(S ** 2, axis=1)
        voiced = (ac[:, sr // 400:sr // 80].max(1) / (ac[:, 0] + 1e-12) > 0.5) & (e_db > e_db.max() - 40)
        busy = np.convolve(voiced, np.ones(2 * int(0.15 * sr / hop) + 1), "same") > 0
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    out, last = [], -1.0
    for i in np.flatnonzero((flux > thr) & ~busy):
        if t[i] - last < min_gap or not (flux[i] >= flux[max(0, i - 2):i + 3]).all():
            continue
        win = slice(i, min(len(t), i + int(0.6 * sr / hop)))
        e = e_db[win]
        dur = (e > e.max() - 12).sum() * hop / sr  # durée « audible » de l'événement
        spec = S[win].mean(0) + 1e-12
        cen = float((freqs * spec).sum() / spec.sum())
        flat = float(np.exp(np.log(spec).mean()) / spec.mean())  # 1 = bruit, 0 = son pur
        pk = int(np.argmax(e))
        if dur < 0.06:
            guess = "click" if cen > 2500 else "pop"
        elif flat < 0.05 and cen > 800:
            guess = "ding"
        elif cen < 600:
            guess = "impact"
        elif pk > len(e) * 0.6:
            guess = "riser"
        else:
            guess = "whoosh"
        out.append({"at": round(float(t[i]), 3), "type_guess": guess, "force_db": round(float(e.max()), 1)})
        last = t[i]
    return out


# ---------- pack de bruitages synthétisés ----------

def _sweep_noise(dur, f0, f1, f2, width, seed, sr=SR):
    """Bruit filtré dont la bande suit f0 → f1 → f2 (log) : base des whoosh/riser."""
    rng = np.random.default_rng(seed)
    n, blk = int(dur * sr), 512
    out = np.zeros(n + 2 * blk)
    win = np.hanning(2 * blk)
    freqs = np.fft.rfftfreq(2 * blk, 1 / sr)
    lf = np.log(np.maximum(freqs, 1))
    for b in range(0, n, blk):
        u = b / n
        fc = np.exp(np.interp(u, [0, 0.5, 1], np.log([f0, f1, f2])))
        spec = np.fft.rfft(rng.standard_normal(2 * blk) * win) * np.exp(-0.5 * ((lf - np.log(fc)) / width) ** 2)
        out[b:b + 2 * blk] += np.fft.irfft(spec) * win
    return out[:n]


def _norm(x, peak_db=-3.0):
    return x / (np.abs(x).max() + 1e-12) * 10 ** (peak_db / 20)


def _stereo_pan(x, start=-0.6, end=0.6):
    p = np.linspace(start, end, len(x))
    return np.stack([x * np.sqrt((1 - p) / 2), x * np.sqrt((1 + p) / 2)], 1)


def synth_sfx(dest=SFX_DIR):
    sr = SR
    t = lambda d: np.arange(int(d * sr)) / sr  # noqa: E731

    def env(d, peak, a=2.0, b=2.0):
        u = t(d) / d
        return np.where(u < peak, (u / peak) ** a, ((1 - u) / (1 - peak)) ** b)

    def pop(f_hi, f_lo, d):
        tt = t(d)
        ph = 2 * np.pi * np.cumsum(f_lo + (f_hi - f_lo) * np.exp(-tt / 0.012)) / sr
        return np.sin(ph) * np.exp(-tt / (d / 5)) * (1 - np.exp(-tt / 0.0015))

    rng = np.random.default_rng(7)
    sounds = {
        "whoosh": _stereo_pan(_sweep_noise(0.7, 250, 2200, 500, 0.45, 1) * env(0.7, 0.55)),
        "whoosh_2": _stereo_pan(_sweep_noise(0.5, 400, 3500, 900, 0.35, 2) * env(0.5, 0.6, 2.5, 1.5), 0.6, -0.6),
        "whoosh_3": _stereo_pan(_sweep_noise(0.9, 150, 1200, 300, 0.6, 3) * env(0.9, 0.5), -0.3, 0.3),
        "swoosh": _stereo_pan(_sweep_noise(0.25, 900, 5000, 2000, 0.3, 4) * env(0.25, 0.4, 1.5, 2.5)),
        "pop": pop(900, 280, 0.12),
        "pop_2": pop(1600, 600, 0.09),
        "click": (np.sin(2 * np.pi * 2400 * t(0.03)) * 0.6 + rng.standard_normal(int(0.03 * sr)) * 0.4)
        * np.exp(-t(0.03) / 0.004),
        "impact": np.tanh(2.5 * (np.sin(2 * np.pi * np.cumsum(45 + 90 * np.exp(-t(1.0) / 0.05)) / sr)
                                 * np.exp(-t(1.0) / 0.25)
                                 + _sweep_noise(1.0, 900, 300, 100, 0.5, 5) * 4 * np.exp(-t(1.0) / 0.06))),
        "ding": sum(a * np.sin(2 * np.pi * 1320 * r * t(1.4)) * np.exp(-t(1.4) / d)
                    for r, a, d in ((1, 1, 0.5), (2.76, 0.4, 0.2), (5.4, 0.2, 0.08), (8.93, 0.1, 0.04)))
        * (1 - np.exp(-t(1.4) / 0.002)),
        "riser": _stereo_pan(_sweep_noise(2.0, 200, 2000, 8000, 0.4, 6) * (t(2.0) / 2.0) ** 2.5
                             + np.sin(2 * np.pi * np.cumsum(np.geomspace(150, 1200, int(2 * sr))) / sr)
                             * 0.15 * (t(2.0) / 2.0) ** 2, -0.2, 0.2),
    }
    for name, x in sounds.items():
        x = _fade(_norm(np.asarray(x, np.float64)), sr, 0.0, 0.01)
        save(Path(dest) / f"{name}.wav", x.astype(np.float32))
    return sorted(sounds)


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(description="Son : nettoyage de la voix, analyse, bruitages.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("nettoyer", help="nettoie une voix (wav mono 48 kHz en sortie)")
    n.add_argument("entree")
    n.add_argument("sortie")
    n.add_argument("--mode", default="auto", choices=MODES)
    s = sub.add_parser("analyser", help="bruit de fond, SNR, ronflette, réverbération, loudness")
    s.add_argument("entree")
    s.add_argument("--transcript", help="transcript JSON : repère aussi les bruitages hors parole")
    sub.add_parser("generer-sfx", help="recrée assets/sfx/*.wav (sons synthétisés)")
    a = ap.parse_args()
    if a.cmd == "nettoyer":
        r = prepare_voice(a.entree, a.sortie, a.mode)
        print(f"Mode : {r['mode']} ({'nettoyé' if r['applique'] else 'laissé tel quel'}"
              f"{', ' + r['methode'] if r['methode'] else ''})\n"
              f"Bruit de fond : {r['bruit_db']} dB → {r.get('bruit_db_apres', r['bruit_db'])} dB ; SNR {r['snr_db']} dB\n"
              f"Sortie : {a.sortie}")
    elif a.cmd == "analyser":
        r = analyze(a.entree)
        r["lufs"] = round(float(loudness(load(a.entree))["input_i"]), 1)
        if a.transcript:
            if not Path(a.transcript).exists():
                die(f"transcript introuvable : {a.transcript}")
            r["bruitages"] = detect_sfx(a.entree, json.loads(Path(a.transcript).read_text(encoding="utf-8"))["words"])
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print("Bruitages :", ", ".join(synth_sfx()), f"→ {SFX_DIR}")


if __name__ == "__main__":
    main()

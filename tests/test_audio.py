import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audio  # noqa: E402

SR = audio.SR


def fake_speech(dur, talk=((0.3, 3.0), (6.0, 8.0)), seed=0):
    """« Voix » synthétique : syllabes de 0,22 s (harmoniques 120-180 Hz, formants), dans les plages `talk`."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    x = np.zeros_like(t)
    for a, b in talk:
        s = a
        while s + 0.22 < b:
            i, n = int(s * SR), int(0.22 * SR)
            f0 = rng.uniform(120, 180)
            tt = t[:n]
            syl = sum(np.sin(2 * np.pi * f0 * k * tt) * np.exp(-((f0 * k - fm) / 400) ** 2)
                      for k in range(1, 40) for fm in (700, 1200, 2600))
            x[i:i + n] += syl * np.hanning(n)
            s += 0.27
    return (x / np.abs(x).max() * 0.5).astype(np.float32)


def band_db(x, f, t0, t1):
    """Niveau (dB) de la composante à `f` Hz entre t0 et t1 (FFT fenêtrée)."""
    seg = x[int(t0 * SR):int(t1 * SR)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    k = int(round(f * len(seg) / SR))
    return 20 * np.log10(spec[k - 2:k + 3].max() + 1e-12)


def test_nettoyage_baisse_le_bruit_sans_tuer_la_voix(tmp_path):
    rng = np.random.default_rng(1)
    clean = fake_speech(9)
    noisy = clean + rng.standard_normal(len(clean)).astype(np.float32) * 0.01  # SNR ~ 25 dB
    audio.save(tmp_path / "in.wav", noisy)
    r = audio.prepare_voice(tmp_path / "in.wav", tmp_path / "out.wav", "leger")
    assert r["applique"] and r["methode"] == "rnnoise"
    out = audio.load(tmp_path / "out.wav")
    a, b = audio.analyze(tmp_path / "in.wav"), audio.analyze(tmp_path / "out.wav")
    assert a["bruit_db"] - b["bruit_db"] > 8, (a, b)  # mélange 85 % : plafond ~16 dB
    assert abs(a["parole_db"] - b["parole_db"]) < 4, (a, b)
    assert abs(len(out) - len(noisy)) < SR // 100


def test_auto_laisse_une_voix_propre(tmp_path):
    audio.save(tmp_path / "in.wav", fake_speech(9) + np.float32(1e-5) * np.random.default_rng(2).standard_normal(9 * SR))
    r = audio.prepare_voice(tmp_path / "in.wav", tmp_path / "out.wav", "auto")
    assert r["mode"] == "aucun" and not r["applique"]
    assert np.allclose(audio.load(tmp_path / "out.wav"), audio.load(tmp_path / "in.wav"), atol=1e-3)


def test_auto_nettoie_une_voix_sale_et_trouve_la_ronflette(tmp_path):
    t = np.arange(9 * SR) / SR
    x = fake_speech(9) + 0.03 * np.sin(2 * np.pi * 50 * t) + 0.015 * np.sin(2 * np.pi * 100 * t) \
        + 0.01 * np.sin(2 * np.pi * 150 * t)
    audio.save(tmp_path / "in.wav", x)
    r = audio.prepare_voice(tmp_path / "in.wav", tmp_path / "out.wav", "auto")
    assert r["ronflette_hz"] == 50 and r["applique"]
    assert band_db(audio.load(tmp_path / "out.wav"), 50, 3.5, 5.5) < band_db(x, 50, 3.5, 5.5) - 20


def test_mode_inconnu_refuse(tmp_path):
    with pytest.raises(SystemExit):
        audio.prepare_voice(tmp_path / "x.wav", tmp_path / "y.wav", "turbo")


def mix_workdir(tmp_path, sfx=(), music=True):
    voice = fake_speech(10)
    audio.save(tmp_path / "voix.wav", voice)
    t = np.arange(12 * SR) / SR
    (tmp_path / "musique").mkdir()
    audio.save(tmp_path / "musique" / "m.wav", 0.3 * np.sin(2 * np.pi * 97 * t))
    edl = {"format": {"fps": 30}, "music": {"source": "musique/m.wav"} if music else None, "sfx": list(sfx)}
    return voice, edl


def test_ducking_profondeur_et_remontee_en_pause(tmp_path):
    voice, edl = mix_workdir(tmp_path)
    prof = {"audio": {"lufs_cible": -14, "musique_db_sous_voix": -18}}
    r = audio.finish_mix(tmp_path / "voix.wav", edl, prof, tmp_path, tmp_path / "mix.wav")
    y = audio.load(tmp_path / "mix.wav")
    assert r["musique"] and r["musique_db_sous_voix"] == -18
    parle, pause = band_db(y, 97, 1.0, 2.6), band_db(y, 97, 4.0, 5.5)
    assert 6 <= pause - parle <= 10, (parle, pause)
    # musique ~18 dB sous la voix pendant la parole (niveaux RMS des deux composantes)
    v = audio._active_rms_db(voice[SR:int(2.6 * SR)])
    m = audio._active_rms_db((0.3 * np.sin(2 * np.pi * 97 * np.arange(SR) / SR)).astype(np.float32))
    g = audio.duck_envelope(audio.speech_mask(voice), v + -18 - m)
    assert abs(g[150] - (v - 18 - m)) < 0.5  # en pleine parole : pile la consigne
    assert abs(r["lufs"] - -14) <= 1 and r["true_peak_db"] <= -0.9


def test_pas_de_pompage_sur_une_respiration():
    mask = np.zeros(500, bool)
    mask[50:200] = True
    mask[230:400] = True  # respiration de 0,3 s
    m = audio._close(mask, 0.6, False)
    assert m[50:400].all()
    g = audio.duck_envelope(m, -20)
    assert g[49:400].max() == -20 and g[34] == -12  # descente anticipée : finie au premier mot
    assert g[400 + 39] > -12.5 and g[400 + 20] < -14 and g[-1] == -12  # remonte en ~400 ms à la fin


@pytest.mark.parametrize("cible", [-14, -16])
def test_loudness_finale(tmp_path, cible):
    _, edl = mix_workdir(tmp_path, music=False)
    r = audio.finish_mix(tmp_path / "voix.wav", edl, {"audio": {"lufs_cible": cible}}, tmp_path, tmp_path / "mix.wav")
    assert abs(r["lufs"] - cible) <= 1 and r["true_peak_db"] <= -0.9
    assert not r["musique"]


def test_bruitage_place_au_bon_moment(tmp_path):
    audio.save(tmp_path / "voix.wav", fake_speech(5, talk=((0.3, 1.5), (3.5, 4.8))))
    edl = {"format": {"fps": 30}, "music": None, "sfx": [{"type": "click", "at": 2.5, "volume_db": -14},
                                                         {"type": "inexistant", "at": 1.0}]}
    r = audio.finish_mix(tmp_path / "voix.wav", edl, {}, tmp_path, tmp_path / "mix.wav")
    y = audio.load(tmp_path / "mix.wav")
    pause = np.abs(y[int(1.8 * SR):int(3.0 * SR)])
    onset = 1.8 + np.argmax(pause > pause.max() * 0.3) / SR
    assert abs(onset - (2.5 - 1 / 30 - audio.ancre(str(audio.SFX_DIR / "click.wav")))) < 0.01, onset
    assert r["sfx_manquants"] == ["inexistant"] and len(r["sfx_places"]) == 1


def test_detect_sfx_hors_parole(tmp_path):
    voice = fake_speech(8, talk=((0.3, 2.5), (5.5, 7.5)))
    for name, at in (("whoosh", 3.0), ("pop", 4.6), ("click", 1.0)):  # le click tombe dans un mot : ignoré
        fx = audio.load(audio.SFX_DIR / f"{name}.wav")
        i = int(at * SR)
        voice[i:i + len(fx)] += fx[:len(voice) - i] * 0.5
    audio.save(tmp_path / "ref.wav", voice)
    words = [{"start": 0.3, "end": 2.5}, {"start": 5.5, "end": 7.5}]
    found = audio.detect_sfx(tmp_path / "ref.wav", words)
    assert any(abs(f["at"] - 3.0) < 0.3 and f["type_guess"] in ("whoosh", "riser") for f in found), found
    assert any(abs(f["at"] - 4.6) < 0.05 and f["type_guess"] in ("pop", "click") for f in found), found
    assert not any(0.3 <= f["at"] <= 2.5 for f in found)


def test_pack_sfx_complet_et_leger():
    noms = {p.stem for p in audio.SFX_DIR.glob("*.wav")}
    assert {"whoosh", "pop", "impact", "ding", "click", "riser", "swoosh"} <= noms
    assert sum(p.stat().st_size for p in audio.SFX_DIR.glob("*.wav")) < 3e6
    lic = (audio.SFX_DIR / "LICENCES.md").read_text(encoding="utf-8")
    assert all(n in lic for n in noms)


def test_cli_analyser(tmp_path, capsys, monkeypatch):
    audio.save(tmp_path / "in.wav", fake_speech(4, talk=((0.2, 3.5),)))
    monkeypatch.setattr(sys, "argv", ["audio.py", "analyser", str(tmp_path / "in.wav")])
    audio.main()
    r = json.loads(capsys.readouterr().out)
    assert {"bruit_db", "snr_db", "lufs"} <= set(r)


@pytest.mark.parametrize("voix", ["sinus", "muette"])
def test_sinus_pur_ou_voix_muette_jamais_de_nan(tmp_path, voix):
    """Retour lot D : sinus pur + silence (rushs de test) ou rush sans son → mix silencieux / NaN."""
    t = np.arange(6 * SR) / SR
    x = 0.125 * np.sin(2 * np.pi * 440 * t) * (t < 4) if voix == "sinus" else np.zeros_like(t)
    audio.save(tmp_path / "v.wav", x)
    for mode in ("auto", "leger", "fort"):
        audio.prepare_voice(tmp_path / "v.wav", tmp_path / "p.wav", mode)
        assert np.isfinite(audio.load(tmp_path / "p.wav")).all()
    _, edl = mix_workdir(tmp_path)
    edl["sfx"] = [{"type": "whoosh", "at": 1.0}]
    r = audio.finish_mix(tmp_path / "v.wav", edl, {"audio": {"lufs_cible": -14}}, tmp_path, tmp_path / "mix.wav")
    y = audio.load(tmp_path / "mix.wav")
    assert np.isfinite(y).all() and abs(r["lufs"] - -14) <= 1, r  # la musique seule est audible et normalisée


def test_detect_sfx_sans_transcript_ignore_la_parole(tmp_path):
    voice = fake_speech(8, talk=((0.3, 2.5), (5.5, 7.5)))
    fx = audio.load(audio.SFX_DIR / "whoosh.wav")
    voice[3 * SR:3 * SR + len(fx)] += fx * 0.5
    audio.save(tmp_path / "ref.wav", voice)
    found = audio.detect_sfx(tmp_path / "ref.wav", [])
    assert any(abs(f["at"] - 3.0) < 0.3 for f in found), found
    assert not any(0.4 <= f["at"] <= 2.4 or 5.6 <= f["at"] <= 7.4 for f in found), found


@pytest.mark.parametrize("poison", [np.nan, 1.7e3])
def test_rnnoise_derniere_trame_corrompue_jamais_muet(tmp_path, monkeypatch, poison):
    """arnndn (ffmpeg 9) rend parfois sa dernière trame en NaN ou en valeurs ~1e3 (au hasard) : la voix nettoyée
    ne doit jamais devenir muette (bug vu en vrai : piste à -70 LUFS)."""
    rng = np.random.default_rng(1)
    noisy = fake_speech(9) + rng.standard_normal(9 * SR).astype(np.float32) * 0.01
    audio.save(tmp_path / "in.wav", noisy)
    vrai = audio.load_after

    def corrompu(x, af, cwd=None):
        y = vrai(x, af, cwd=cwd).copy()
        if "arnndn" in af:
            y[-480:] = poison
        return y
    monkeypatch.setattr(audio, "load_after", corrompu)
    r = audio.prepare_voice(tmp_path / "in.wav", tmp_path / "out.wav", "leger")
    b = audio.analyze(tmp_path / "out.wav")
    assert r["methode"] == "rnnoise" and abs(audio.analyze(tmp_path / "in.wav")["parole_db"] - b["parole_db"]) < 4


def test_nettoyage_true_false(tmp_path):
    audio.save(tmp_path / "in.wav", fake_speech(4, talk=((0.3, 3.0),)))
    assert audio.prepare_voice(tmp_path / "in.wav", tmp_path / "o.wav", False)["mode"] == "aucun"


def test_temps_forts_120_bpm(tmp_path):
    """Clics à 120 BPM (un toutes les 0,5 s, premier à 0,25 s) : grille retrouvée à ±25 ms ; calage borné."""
    sr = 11025
    x = np.zeros(sr * 8, np.float32)
    for k in range(16):
        i = int((0.25 + 0.5 * k) * sr)
        x[i:i + 200] = np.random.default_rng(k).uniform(-0.9, 0.9, 200)
    f = tmp_path / "clics.wav"
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    t = audio.beats(f, 8)
    assert len(t) >= 12 and all(abs((b - 0.25) / 0.5 - round((b - 0.25) / 0.5)) * 0.5 < 0.025 for b in t)
    assert audio.cale_sur_temps(1.30, t) == pytest.approx(1.25, abs=0.03)  # 50 ms avant : calé
    assert audio.cale_sur_temps(1.45, t) == 1.45  # temps fort à 1,25 trop loin (> 0,15 s), 1,75 en retard : inchangé


def test_ancre_selon_le_type(tmp_path):
    """Point fort : crête d'un whoosh, fin d'un riser, attaque d'un impact (aussi pour un son déposé)."""
    t = np.arange(SR) / SR
    bruit = np.random.default_rng(0).standard_normal(SR).astype(np.float32)
    audio.save(tmp_path / "whoosh_perso.wav", bruit * np.exp(-((t - 0.6) / 0.08) ** 2) * 0.5)
    audio.save(tmp_path / "riser.wav", bruit * t * 0.5)
    audio.save(tmp_path / "impact.wav", np.r_[np.zeros(SR // 5), bruit[:SR // 2] * np.exp(-t[:SR // 2] / 0.1)] * 0.5)
    assert abs(audio.ancre(str(tmp_path / "whoosh_perso.wav")) - 0.6) < 0.03
    assert audio.ancre(str(tmp_path / "riser.wav")) == pytest.approx(1.0, abs=1e-3)
    assert abs(audio.ancre(str(tmp_path / "impact.wav")) - 0.2) < 0.03
    assert audio.depart(tmp_path / "riser.wav", 3.1, 30) == pytest.approx(3.1 - 1 / 30 - 1.0, abs=1e-3)  # finit sur la coupe


def test_carton_sonorise_comme_appris():
    appris = {"impact": True, "intervalle_s": 0.267, "premier_tic_s": 0.29, "tic": "whoosh", "riser": True,
              "niveaux_db": {"impact": -8, "tic": -20}}
    fx = audio.carton_sfx({"at": 0.0, "duration": 3.1}, appris, 30)
    assert fx[0] == {"type": "impact", "at": 0.0, "volume_db": -8, "duree_s": 0.29, "carton": True}
    tics = [x for x in fx if x["type"] not in ("impact", "riser")]
    assert len(tics) == 11 and tics[0]["at"] == 0.29 and tics[-1]["at"] <= 3.1 - 0.13
    assert {x["type"] for x in tics} == {"swoosh"}  # 0,25 s : le seul souffle qui tient dans 0,27 s
    assert fx[-1]["type"] == "riser" and fx[-1]["at"] == 3.1 and fx[-1]["volume_db"] == audio.NIVEAUX_CARTON["riser"]
    assert audio.carton_sfx({"at": 0, "duration": 3}, {"riser": True}, 30) == [
        {"type": "riser", "at": 3.0, "volume_db": audio.NIVEAUX_CARTON["riser"], "carton": True}]


def test_carton_appris_sur_une_reference(tmp_path):
    """Référence synthétique : coup à 0, clics tous les 0,25 s, voix après 3 s → rôles, intervalle, niveaux."""
    x = np.zeros(6 * SR, np.float32)
    x[:int(0.2 * SR)] += audio.load(audio.SFX_DIR / "impact.wav")[:int(0.2 * SR)] * 0.9
    clic = audio.load(audio.SFX_DIR / "click.wav") * 0.1
    for k in range(10):
        i = int((0.3 + 0.25 * k) * SR)
        x[i:i + len(clic)] += clic
    x[3 * SR:] += fake_speech(3, talk=((0.1, 2.9),))
    audio.save(tmp_path / "ref.wav", x)
    found = [{"t": 0.0, "type": "impact"}] + [{"t": round(0.3 + 0.25 * k, 3), "type": "click"} for k in range(10)]
    c = audio.carton_appris(tmp_path / "ref.wav", found, 3.0)
    assert c["impact"] and not c["riser"] and c["intervalle_s"] == 0.25 and c["premier_tic_s"] == 0.3
    assert c["tic"] == "click" and c["niveaux_db"]["impact"] > c["niveaux_db"]["tic"] + 10
    assert audio.carton_appris(tmp_path / "ref.wav", found[:1], 3.0) is None


def test_musique_sans_niveau_appris_defaut_commun(tmp_path):
    """DA sans musique (musique_db_sous_voix None, ancien musique_lufs par défaut) : MUSIQUE_DB_DEFAUT, pas -16."""
    _, edl = mix_workdir(tmp_path)
    prof = {"audio": {"lufs_cible": -14, "musique": False, "musique_db_sous_voix": None, "musique_lufs": -30}}
    r = audio.finish_mix(tmp_path / "voix.wav", edl, prof, tmp_path, tmp_path / "mix.wav")
    assert r["musique_db_sous_voix"] == audio.MUSIQUE_DB_DEFAUT == -22


def test_bruitage_ecourte(tmp_path):
    """duree_s : le son s'arrête (fondu court) au lieu de traîner sur les tics suivants."""
    audio.save(tmp_path / "voix.wav", fake_speech(4, talk=((2.5, 3.8),)))
    edl = {"format": {"fps": 30}, "music": None, "sfx": [{"type": "impact", "at": 0.5, "duree_s": 0.3, "volume_db": -8}]}
    audio.finish_mix(tmp_path / "voix.wav", edl, {}, tmp_path, tmp_path / "mix.wav")
    y = np.abs(audio.load(tmp_path / "mix.wav"))
    debut = 0.5 - 1 / 30 - audio.ancre(str(audio.SFX_DIR / "impact.wav"))
    assert y[int((debut + 0.05) * SR):int((debut + 0.2) * SR)].max() > 0.05
    assert y[int((debut + 0.32) * SR):int(2.3 * SR)].max() < 1e-3  # plus rien après 0,3 s

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import transcribe as tr  # noqa: E402

MODEL = os.environ.get("MONTEUR_TEST_MODEL", "tiny")
PHRASES = ["Bonjour à tous.", "Aujourd'hui on parle de montage vidéo.",
           "Aujourd'hui on parle de montage vidéo automatique.", "Merci et à bientôt."]

PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | ? { $_.VoiceInfo.Culture.Name -like 'fr*' } | select -First 1
if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }
$p = New-Object System.Speech.Synthesis.PromptBuilder
$p.AppendBreak([TimeSpan]::FromMilliseconds(800))
foreach ($l in [IO.File]::ReadAllLines($env:PHRASES_FILE)) { $p.AppendText($l); $p.AppendBreak([TimeSpan]::FromMilliseconds(1200)) }
$s.SetOutputToWaveFile($env:WAV_OUT); $s.Speak($p); $s.Dispose()
"""


def make_media(folder, phrases=PHRASES, name="rush"):
    """Vidéo testsrc2 + voix SAPI française (une phrase volontairement reprise)."""
    folder.mkdir(parents=True, exist_ok=True)
    wav, mp4 = folder.parent / f"{name}.wav", folder / f"{name}.mp4"
    (folder.parent / "phrases.txt").write_text("\n".join(phrases), encoding="utf-8-sig")
    env = dict(os.environ, PHRASES_FILE=str(folder.parent / "phrases.txt"), WAV_OUT=str(wav))
    subprocess.run(["powershell", "-NoProfile", "-Command", PS], env=env, check=True)
    dur = common.probe(wav)["duration"]
    common.run([common.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=540x960:rate=30:d={dur}",
                "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(mp4)])
    return mp4


_SHARED = {}  # test_timeline_view importe ces fixtures : on ne génère/transcrit qu'une fois


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    if not shutil.which("powershell"):
        pytest.skip("synthèse vocale Windows (SAPI) indisponible")
    if "media" not in _SHARED:
        root = tmp_path_factory.mktemp("work")
        mp4 = make_media(root / "rushs")
        (root / "ma-da").mkdir()
        (root / "ma-da" / "lexique.txt").write_text("montage\n# commentaire\n", encoding="utf-8")
        _SHARED["media"] = root, mp4
    return _SHARED["media"]


def run_tr(root, *extra):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "transcribe.py"), "rushs", "--model", MODEL,
                        "--root", ".", *extra], cwd=root, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture(scope="session")
def transcribed(media):
    if "tr" not in _SHARED:
        root, mp4 = media
        out = run_tr(root)  # ma-da/lexique.txt lu par défaut
        t = json.loads((root / ".monteur" / "transcripts" / "rush.json").read_text(encoding="utf-8"))
        _SHARED["tr"] = root, mp4, out, t
    return _SHARED["tr"]


def test_transcript_format(transcribed):
    _, _, out, t = transcribed
    assert set(t) == {"source", "duration", "language", "model", "words", "silences"}
    assert all(set(w) <= {"w", "start", "end", "p", "filler"} for w in t["words"])
    assert t["source"] == "rushs/rush.mp4" and t["model"] == MODEL and t["language"] == "fr"
    assert len(t["words"]) >= 15
    starts = [w["start"] for w in t["words"]]
    assert starts == sorted(starts)
    assert all(w["start"] <= w["end"] for w in t["words"])
    assert all(a["end"] <= b["start"] for a, b in zip(t["words"], t["words"][1:]))
    assert "Résumé" in out and "reprise" in out


def test_silences(transcribed):
    _, _, _, t = transcribed
    # 0.8 s au début et 1.2 s entre chaque phrase ; recalage silencedetect -> pauses quasi complètes
    assert t["silences"][0]["start"] == 0.0
    assert sum(1 for s in t["silences"] if s["end"] - s["start"] > 0.9) >= 3, t["silences"]


def test_retake_flagged(transcribed):
    root, _, _, _ = transcribed
    lines = (root / ".monteur" / "takes.md").read_text(encoding="utf-8").splitlines()
    assert all("[rush.mp4 " in l for l in lines)
    flagged = [l for l in lines if l.startswith("(reprise?)")]
    assert len(flagged) == 1 and "automatique" not in flagged[0].lower(), lines
    assert "Aujourd'hui" in flagged[0]


def test_cache(transcribed):
    root, _, _, _ = transcribed
    assert "cache" in run_tr(root)


def test_helpers():
    w = lambda s, a, b, p=0.9: {"w": s, "start": a, "end": b, "p": p}
    words = [w("On", 0.5, 0.7), w("y", 0.7, 0.8), w("va", 0.8, 1.0), w("On", 2.0, 2.2), w("y", 2.2, 2.3),
             w("va.", 2.3, 2.5, 0.3)]
    assert tr.find_silences(words, 3.0) == [{"start": 0.0, "end": 0.5}, {"start": 1.0, "end": 2.0},
                                            {"start": 2.5, "end": 3.0}]
    lines, n = tr.takes_lines({"source": "rushs/a.mp4", "words": words})
    assert n == 1 and lines == ["(reprise?) [a.mp4 0.50-1.00] On y va", "[a.mp4 2.00-2.50] On y va.?"]
    # hésitations visibles dans takes.md, ignorées pour reconnaître une reprise
    words[3:3] = [{"w": "[euh]", "start": 1.5, "end": 1.7, "p": 0.0, "filler": True}]
    words[0:0] = [{"w": "euh", "start": 0.1, "end": 0.3, "p": 0.8, "filler": True}]
    lines, n = tr.takes_lines({"source": "rushs/a.mp4", "words": words})
    assert n == 1 and lines[0] == "(reprise?) [a.mp4 0.10-1.00] [euh] On y va" and "[euh]" in lines[1], lines


def test_same_name_other_folder(transcribed):
    root, mp4, _, t = transcribed
    (root / "mes-videos").mkdir(exist_ok=True)
    shutil.copy(mp4, root / "mes-videos" / "rush.mp4")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "transcribe.py"), "mes-videos", "--model", MODEL,
                        "--takes", ".monteur/takes_mes-videos.md"], cwd=root, capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    tdir = root / ".monteur" / "transcripts"
    assert json.loads((tdir / "mes-videos_rush.json").read_text(encoding="utf-8"))["source"] == "mes-videos/rush.mp4"
    assert json.loads((tdir / "rush.json").read_text(encoding="utf-8"))["source"] == "rushs/rush.mp4"
    assert "[rush.mp4 " in (root / ".monteur" / "takes_mes-videos.md").read_text(encoding="utf-8")


def test_snap_to_silences():
    words = [{"w": "tous.", "start": 1.0, "end": 2.1, "p": 0.9}, {"w": "Salut", "start": 3.0, "end": 3.5, "p": 0.9}]
    tr.snap_to_silences(words, [(1.6, 3.2)])
    assert (words[0]["end"], words[1]["start"]) == (1.6, 3.2)


def test_glue_fragments_whisper():
    w = lambda t, a, b, p=1.0: {"w": t, "start": a, "end": b, "p": p}
    out = tr.glue([w("C", 1, 1.1, 0.1), w("'est", 1.1, 1.3), w("Abonne", 2, 2.3), w("-toi", 2.3, 2.5), w("-", 3, 3)])
    assert [x["w"] for x in out] == ["C'est", "Abonne-toi", "-"]
    assert out[0]["start"] == 1 and out[0]["end"] == 1.3 and out[0]["p"] == 1.0


def test_refine_edges_extends_weak_tails():
    sr = 16000
    t = np.arange(int(3 * sr)) / sr
    audio = 0.001 * np.random.default_rng(0).standard_normal(len(t)).astype(np.float32)  # bruit de fond -60 dB
    audio[int(0.5 * sr):int(1.0 * sr)] += 0.3 * np.sin(2 * np.pi * 220 * t[:int(0.5 * sr)])  # voyelle forte
    audio[int(1.0 * sr):int(1.15 * sr)] += 0.02 * np.sin(2 * np.pi * 4000 * t[:int(0.15 * sr)])  # « s » final faible
    audio[int(1.6 * sr):int(2.0 * sr)] += 0.3 * np.sin(2 * np.pi * 220 * t[:int(0.4 * sr)])
    w = lambda s, a, b: {"w": s, "start": a, "end": b, "p": 0.9}
    words = tr.refine_edges([w("tous", 0.55, 0.95), w("Salut", 1.6, 1.9)], audio)
    assert 1.13 <= words[0]["end"] <= 1.2 and 0.48 <= words[0]["start"] <= 0.55  # fin prolongée jusqu'au « s »
    assert 1.97 <= words[1]["end"] <= 2.03 and words[1]["start"] >= words[0]["end"] + 0.02
    # jamais raccourci, jamais sur le voisin
    touching = tr.refine_edges([w("a", 0.5, 0.9), w("b", 0.92, 1.0)], audio)
    assert touching[0]["end"] == 0.9 and touching[1]["start"] == 0.92


def test_vad_fillers(monkeypatch):
    import faster_whisper.vad as fv
    sr = 16000
    chunks = [(0.5, 1.6), (2.0, 2.4), (3.0, 3.05), (4.0, 6.0), (7.0, 7.3)]
    monkeypatch.setattr(fv, "get_speech_timestamps",
                        lambda audio, opts, sampling_rate: [{"start": int(a * sr), "end": int(b * sr)} for a, b in chunks])
    w = lambda s, a, b: {"w": s, "start": a, "end": b, "p": 0.9}
    words = [w("Alors", 0.5, 0.9), w("voilà.", 1.4, 1.6), w("Ok", 7.0, 7.12)]
    out = tr.vad_fillers(words, np.zeros(8 * sr, dtype=np.float32))
    fill = [(x["start"], x["end"]) for x in out if x.get("filler")]
    # 1.0-1.3 : parole entre deux mots, isolée ; 2.0-2.4 : isolée ; 3.0 trop court ; 4-6 trop long (phrase sautée ?) ;
    # 7.22-7.3 : reste collé au mot « Ok », trop court
    assert fill == [(1.0, 1.3), (2.0, 2.4)]
    assert [x["w"] for x in out] == ["Alors", "[euh]", "voilà.", "[euh]", "Ok"]


def test_fillers_real_audio(tmp_path):
    """Voix SAPI avec un « euh » isolé : il doit apparaître dans le transcript, marqué filler, entre les deux phrases."""
    if not shutil.which("powershell"):
        pytest.skip("synthèse vocale Windows (SAPI) indisponible")
    pytest.importorskip("onnx_asr")
    make_media(tmp_path / "rushs", ["Alors on commence.", "euh", "Le café se boit chaud."], name="euh")
    # moteur par défaut (Parakeet) : Whisper tiny entend « peu » à la place du « euh », un vrai mot qu'on ne coupe pas
    run_tr(tmp_path, "--model", tr.PARAKEET)
    t = json.loads((tmp_path / ".monteur" / "transcripts" / "euh.json").read_text(encoding="utf-8"))
    fillers = [w for w in t["words"] if w.get("filler")]
    before = next(w for w in t["words"] if "commence" in w["w"].lower())
    after = next(w for w in t["words"] if "café" in w["w"].lower())
    assert fillers and all(before["end"] <= f["start"] and f["end"] <= after["start"] for f in fillers), t["words"]
    assert "[" in (tmp_path / ".monteur" / "takes.md").read_text(encoding="utf-8")

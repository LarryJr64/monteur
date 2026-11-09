"""Dépôt en vrac : chaque fichier reconnu (parle, plan, voix off, musique) et le bon mode de montage."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import trier  # noqa: E402
from common import ffmpeg  # noqa: E402


def ff(*args):
    subprocess.run([ffmpeg(), "-y", "-v", "error", *args], check=True)


def transcript(root, src, n, p=0.9):
    ws = [{"w": "mot", "start": 0.3 * i, "end": 0.3 * i + 0.2, "p": p} for i in range(n)]
    (root / ".monteur/transcripts").mkdir(parents=True, exist_ok=True)
    (root / f".monteur/transcripts/{Path(src).stem}.json").write_text(
        json.dumps({"source": src, "words": ws}), encoding="utf-8")


def test_depot_en_vrac(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "depot"
    d.mkdir()
    vid = ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=4", "-f", "lavfi",
           "-i", "sine=duration=4", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]
    ff(*vid, str(d / "moi.mp4"))
    ff(*vid, str(d / "rue.mp4"))
    ff("-f", "lavfi", "-i", "sine=duration=4", str(d / "memo.m4a"))
    ff("-f", "lavfi", "-i", "sine=duration=4", str(d / "fond.mp3"))
    (d / "trame.txt").write_text("1. le problème 2. la solution", encoding="utf-8")
    transcript(tmp_path, "depot/moi.mp4", 10)
    transcript(tmp_path, "depot/rue.mp4", 1)
    transcript(tmp_path, "depot/memo.m4a", 10)
    transcript(tmp_path, "depot/fond.mp3", 8, p=0.3)  # chanson : des mots peu sûrs
    trier.main(["depot"])
    tri = (tmp_path / ".monteur/tri.md").read_text(encoding="utf-8")
    assert "Mode : **voix off**" in tri
    for nom, typ in (("moi.mp4", "parle"), ("rue.mp4", "plan"), ("memo.m4a", "voix off"), ("fond.mp3", "musique"),
                     ("trame.txt", "trame")):
        assert f"| {nom} | {typ}" in tri, (nom, tri)


def test_modes():
    assert trier.mode(["parle", "plan", "musique"]) == "face caméra"
    assert trier.mode(["plan", "musique"]) == "aucune voix"

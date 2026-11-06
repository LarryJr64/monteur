import subprocess
import sys
from pathlib import Path

from PIL import Image

from test_transcribe import ROOT, media, transcribed  # noqa: F401  (fixtures partagées)


def test_png(transcribed):
    root, mp4, _, _ = transcribed
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline_view.py"), "rushs/rush.mp4",
                        "--de", "1", "--a", "6", "--root", "."], cwd=root, capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    png = root / ".monteur" / "vues" / "rush_1-6.png"
    img = Image.open(png)
    assert img.width == 1600 and img.height > 300
    assert "8 vignettes" in r.stdout and " 0 mots" not in r.stdout


def test_bad_range(media):
    root, _ = media
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "timeline_view.py"), "rushs/rush.mp4",
                        "--de", "50", "--a", "40"], cwd=root, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode != 0 and "plage vide" in r.stderr

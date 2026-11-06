"""Outils partagés : trouver ffmpeg, lancer des commandes, lire les métadonnées."""
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows redirige stdout en cp1252 : les accents arriveraient cassés à Claude
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def _find(name):
    found = shutil.which(name)
    if found:
        return found
    # winget installe ffmpeg sans toujours mettre à jour le PATH du terminal courant
    local = os.environ.get("LOCALAPPDATA", "")
    hits = glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", name + ".exe"))
    if hits:
        return sorted(hits)[-1]
    # copie statique posée par doctor.py --installer, puis Homebrew (absent du PATH des apps Mac lancées du Dock)
    exe = name + (".exe" if os.name == "nt" else "")
    for d in (Path.home() / ".cache" / "monteur" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        if (d / exe).is_file():
            return str(d / exe)
    die(f"{name} introuvable. Lance : python scripts/doctor.py (ou installe ffmpeg : winget install Gyan.FFmpeg)")


def ffmpeg():
    return _find("ffmpeg")


def ffprobe():
    return _find("ffprobe")


def die(msg, code=1):
    print(f"ERREUR : {msg}", file=sys.stderr)
    sys.exit(code)


def run(args, capture=True):
    """Lance une commande ; renvoie stdout+stderr. Échoue avec un message lisible."""
    r = subprocess.run(args, capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = (r.stderr or "")[-1500:]
        die(f"commande échouée ({Path(args[0]).name}) :\n{tail}")
    return (r.stdout or "") + (r.stderr or "")


def probe(path):
    """Durée, taille, fps, présence audio d'un média."""
    out = run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out[out.index("{"):])
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {"duration": float(data["format"].get("duration", 0)), "has_audio": a is not None}
    if v:
        num, den = v.get("avg_frame_rate", "0/1").split("/")
        info.update(width=int(v["width"]), height=int(v["height"]),
                    fps=round(float(num) / float(den), 3) if float(den) else 0.0)
        # vidéos de téléphone : la rotation inverse largeur/hauteur
        rot = abs(int(next((sd.get("rotation", 0) for sd in v.get("side_data_list", []) if "rotation" in sd), 0)))
        if rot in (90, 270):
            info["width"], info["height"] = info["height"], info["width"]
    return info


def work_dir(root="."):
    d = Path(root) / ".monteur"
    for sub in ("transcripts", "vues", "rapports"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def find_transcript(root, source):
    """Transcript d'un média : `<stem>.json` ou `<dossier>_<stem>.json` (voir transcribe.py) ;
    le champ `source` fait foi (comparé en chemin résolu). None si absent."""
    root, want = Path(root), (Path(root) / source).resolve()
    tdir = root / ".monteur" / "transcripts"
    src = Path(source)
    names = [f"{src.parent.name}_{src.stem}.json", f"{src.stem}.json"]
    names += sorted(f.name for f in tdir.glob(f"*{src.stem}.json") if f.name not in names) if tdir.is_dir() else []
    for name in names:
        f = tdir / name
        if f.is_file():
            t = json.loads(f.read_text(encoding="utf-8"))
            if (root / t.get("source", "")).resolve() == want:
                return t
    return None


def whisper_model_path(name):
    """Dossier local du modèle faster-whisper (téléchargé au besoin, une seule fois).
    Windows sans droit de créer des liens symboliques (WinError 1314) : le cache Hugging Face échoue ;
    on retombe sur une copie simple dans ~/.cache/monteur/<modèle>, réutilisée ensuite."""
    from faster_whisper import download_model
    local = Path.home() / ".cache" / "monteur" / name
    if (local / "model.bin").is_file():
        return str(local)
    try:
        return download_model(name)
    except OSError:
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        last = None
        for _ in range(2):  # un second essai couvre les coupures réseau passagères
            try:
                return download_model(name, output_dir=str(local))
            except OSError as e:
                last = e
        raise last


VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi", ".mts"}


def list_videos(folder):
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in VIDEO_EXT)

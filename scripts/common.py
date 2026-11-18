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


def ram_libre_go():
    """Mémoire disponible (Go) ; None si inconnue. Sans dépendance : Windows, Linux, Mac."""
    try:
        if sys.platform == "win32":
            import ctypes

            class M(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("c", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in (
                    "tot", "dispo", "ptot", "pdispo", "vtot", "vdispo", "x")]
            m = M()
            m.l = ctypes.sizeof(M)
            return m.dispo / 2**30 if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else None
        if Path("/proc/meminfo").exists():
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 2**20
        out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout  # Mac
        page = 16384 if "16384" in out.splitlines()[0] else 4096
        pages = sum(int(l.split(":")[1].strip(" .")) for l in out.splitlines()
                    if l.startswith(("Pages free", "Pages inactive", "Pages speculative")))
        return pages * page / 2**30
    except Exception:
        return None


# Ordinateurs à peu de mémoire (ou déjà chargés) : un seul encodage à la fois et ffmpeg sur 2-4 cœurs.
# MONTEUR_ECONOME=1 / 0 force le mode ; sinon il s'active sous 4 Go libres au lancement.
_r = ram_libre_go()
ECONOME = os.environ.get("MONTEUR_ECONOME", "1" if _r is not None and _r < 4 else "0") == "1"
PARALLELE = 1 if ECONOME else 3          # travaux ffmpeg lancés en même temps
FF_THREADS = ["-threads", "2"] if ECONOME else ["-threads", "4"]  # par décodeur / encodeur (12 cœurs = 12 fils et autant d'images en mémoire)


MUSIQUE_DB_DEFAUT = -22  # musique sous la voix (dB) quand ni l'edl ni la DA ne donnent de niveau
LUFS_DIFFUSION = -14.0   # cible de diffusion des réseaux (true peak ≤ -1 dBTP) ; la loudness du pro est informative


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


CADENCES = (23.976, 24, 25, 29.97, 30, 50, 59.94, 60)


def _rate(s):
    num, _, den = (s or "0/1").partition("/")
    return float(num) / float(den or 1) if float(den or 1) else 0.0


def _fps(v):
    """Cadence d'un flux. Fréquence variable (téléphones) : la moyenne (46,8…) ne veut rien dire, on prend la
    cadence nominale (r_frame_rate) ramenée à la cadence standard la plus proche."""
    avg, nom = _rate(v.get("avg_frame_rate")), _rate(v.get("r_frame_rate"))
    if not nom or abs(avg - nom) < 0.01:
        return round(avg or nom, 3)
    c = nom if nom <= 61 else avg
    return min(CADENCES, key=lambda s: abs(s - c))


def tonemap(info):
    """Filtre (avec virgule finale) qui ramène une source HDR en SDR BT.709 ; vide pour une source SDR.
    Sans lui, l'iPhone (HLG par défaut) sort délavé et la DA apprise sur ses vidéos aussi."""
    if not (info or {}).get("hdr"):
        return ""
    # blanc de référence HDR = 203 nits (BT.2408) ; mobius garde gris moyen et blancs, adoucit seulement les hautes
    # lumières (hable assombrissait tout : gris 18 % à 0,37 au lieu de 0,42)
    return ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=mobius:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")


def probe(path):
    """Durée, taille, fps, présence audio d'un média."""
    out = run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out[out.index("{"):])
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {"duration": float(data["format"].get("duration", 0)), "has_audio": a is not None}
    if v:
        info.update(width=int(v["width"]), height=int(v["height"]), fps=_fps(v),
                    hdr=v.get("color_transfer") in ("arib-std-b67", "smpte2084"))  # HLG (iPhone) ou PQ
        # vidéos de téléphone : la rotation inverse largeur/hauteur
        rot = abs(int(next((sd.get("rotation", 0) for sd in v.get("side_data_list", []) if "rotation" in sd), 0)))
        if rot in (90, 270):
            info["width"], info["height"] = info["height"], info["width"]
    return info


# noms de dossiers que les créateurs utilisent déjà (le premier qui existe), sinon le dernier (celui du monteur)
RUSHS = ("A-Roll", "a-roll", "rushs")
BROLL = ("B-Roll", "b-roll", "secondaires", "broll")
MUSIQUE = ("Musique", "music", "musique")


def dossier(root, noms, force=None):
    """Son dossier réel (noms exacts : A-Roll, B-Roll, secondaires…), sinon le dernier nom de la liste."""
    if force:
        return Path(root) / force
    la = {p.name for p in Path(root).iterdir() if p.is_dir()} if Path(root).is_dir() else set()
    return Path(root) / next((n for n in noms if n in la), noms[-1])


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

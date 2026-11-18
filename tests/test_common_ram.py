"""Mode économe : mémoire libre lue sans dépendance, réglages ffmpeg cohérents."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common  # noqa: E402


def test_ram_libre_et_reglages():
    r = common.ram_libre_go()
    assert r is None or 0 < r < 4096
    assert common.PARALLELE == (1 if common.ECONOME else 3)
    assert common.FF_THREADS[0] == "-threads" and int(common.FF_THREADS[1]) in (2, 4)

"""Page de review locale : la vidéo montée, sa frise (rush, plans, inserts, sous-titres) et des notes posées à
l'image près. Les notes s'enregistrent dans .monteur/review/<nom>.json, que la skill corriger lit.
Tout reste sur l'ordinateur (serveur sur 127.0.0.1, rien à installer).

  python scripts/review.py sorties/reel.mp4            → ouvre http://127.0.0.1:8765 dans le navigateur
  python scripts/review.py sorties/reel.mp4 --notes    → affiche les notes enregistrées (pour corriger)"""
import argparse
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import die, probe  # noqa: E402


def srt(path):
    if not path.exists():
        return []
    out = []
    for bloc in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)\s*\n(.*)", bloc, re.S)
        if m:
            g = [int(x) for x in m.groups()[:8]]
            out.append({"de": g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                        "a": g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, "texte": " ".join(m[9].split())})
    return out


def frise(video, root):
    """Ce qu'il y a à chaque instant : morceaux de rush, plans/inserts, sous-titres (temps de la vidéo montée)."""
    p = root / ".monteur" / "edl.rendu.json"
    edl = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if edl.get("output") and Path(edl["output"]).name != video.name:
        edl = {}  # le rendu enregistré est celui d'une autre version : on n'affiche que les sous-titres
    pistes, t = [], 0.0
    for i, s in enumerate(edl.get("segments") or []):
        d = s["end"] - s["start"]
        pistes.append({"piste": "rush", "de": t, "a": t + d, "texte": f"{Path(s['source']).name} {s['start']:.2f}-{s['end']:.2f} s"
                       + (f" ({s['note']})" if s.get("note") else ""), "n": i + 1})
        t += d
    for b in edl.get("broll") or []:
        pistes.append({"piste": "insert" if b.get("texte") else "plan", "de": b["at"], "a": b["at"] + b["duration"],
                       "texte": Path(b["source"]).name + (f" sur « {b['mot']} »" if b.get("mot") else "")})
    pistes += [dict(x, piste="sous-titre") for x in srt(video.with_suffix(".srt"))]
    return pistes


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Review</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--f:#111;--c:#f5f5f5;--g:#888;--a:#ffb300;--rush:#4a6;--plan:#38c;--insert:#c5a;--st:#777}
body{margin:0;background:var(--f);color:var(--c);font:15px system-ui,sans-serif}
main{display:grid;grid-template-columns:minmax(280px,420px) 1fr;gap:20px;padding:16px;max-width:1200px;margin:auto}
@media(max-width:800px){main{grid-template-columns:1fr}}
video{width:100%;max-height:78vh;background:#000;border-radius:8px}
.barre{display:flex;gap:6px;align-items:center;margin:8px 0;flex-wrap:wrap}
button{background:#2a2a2a;color:var(--c);border:1px solid #444;border-radius:6px;padding:6px 10px;cursor:pointer;font:inherit}
button:hover{border-color:var(--a)}#t{font-variant-numeric:tabular-nums;color:var(--a);min-width:7em}
.frise{position:relative;height:72px;background:#1c1c1c;border-radius:6px;cursor:pointer;margin:10px 0}
.frise div{position:absolute;height:14px;border-radius:3px;opacity:.85}.frise .cur{top:0;bottom:0;width:2px;height:auto;background:var(--a);opacity:1}
.frise .note{top:0;width:8px;height:8px;margin-left:-4px;border-radius:50%;background:var(--a);opacity:1}
.leg span{display:inline-block;margin-right:12px;color:var(--g)}.leg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
h2{font-size:15px;color:var(--g);font-weight:600;margin:18px 0 6px}
.ici div{padding:3px 0}.ici b{color:var(--g);font-weight:500;margin-right:6px}
textarea{width:100%;box-sizing:border-box;background:#1c1c1c;color:var(--c);border:1px solid #444;border-radius:6px;padding:8px;font:inherit;min-height:60px}
ul{list-style:none;padding:0;margin:0}li{display:flex;gap:8px;align-items:baseline;padding:6px 0;border-bottom:1px solid #262626}
li a{color:var(--a);cursor:pointer;font-variant-numeric:tabular-nums;min-width:4.5em}li span{flex:1}li button{padding:2px 8px}
#etat{color:var(--g);font-size:13px}kbd{background:#2a2a2a;border-radius:4px;padding:1px 5px;font-size:12px}
</style></head><body><main>
<section><video id="v" src="/video" controls playsinline preload="auto"></video>
<div class="barre"><button data-d="-1">◀ image</button><button id="lec">Lecture</button><button data-d="1">image ▶</button>
<span id="t">0,00 s</span></div>
<div style="color:var(--g);font-size:13px"><kbd>espace</kbd> lecture · <kbd>←</kbd><kbd>→</kbd> une image · <kbd>N</kbd> noter ici</div>
</section>
<section><div class="frise" id="frise"></div>
<div class="leg"><span><i style="background:var(--rush)"></i>rush</span><span><i style="background:var(--plan)"></i>plan</span>
<span><i style="background:var(--insert)"></i>insert animé</span><span><i style="background:var(--st)"></i>sous-titre</span></div>
<h2>À cet instant</h2><div class="ici" id="ici"></div>
<h2>Ta note à <span id="tn">0,00 s</span></h2><textarea id="txt" placeholder="ex. coupe trop tôt, plan trop long, sous-titre mal placé, garde ce silence…"></textarea>
<div class="barre"><button id="aj">Ajouter la note</button><span id="etat"></span></div>
<h2>Notes</h2><ul id="notes"></ul></section></main>
<script>
const P=__PISTES__,FPS=__FPS__,D=__DUREE__;let N=__NOTES__;
const v=document.getElementById('v'),fr=document.getElementById('frise'),$=id=>document.getElementById(id);
const fmt=s=>s.toFixed(2).replace('.',',')+' s',row={rush:0,plan:1,insert:1,'sous-titre':2},col={rush:'--rush',plan:'--plan',insert:'--insert','sous-titre':'--st'};
for(const p of P){const d=document.createElement('div');d.style.cssText=`left:${p.de/D*100}%;width:${Math.max(.3,(p.a-p.de)/D*100)}%;top:${6+row[p.piste]*20}px;background:var(${col[p.piste]})`;d.title=p.texte;fr.appendChild(d)}
const cur=document.createElement('div');cur.className='cur';fr.appendChild(cur);
fr.onclick=e=>{const r=fr.getBoundingClientRect();v.currentTime=(e.clientX-r.left)/r.width*D};
function maj(){const t=v.currentTime;$('t').textContent=fmt(t);$('tn').textContent=fmt(t);cur.style.left=t/D*100+'%';
 $('ici').innerHTML=P.filter(p=>p.de<=t&&t<p.a).map(p=>`<div><b>${p.piste}</b>${esc(p.texte)}</div>`).join('')||'<div style="color:var(--g)">—</div>'}
const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
v.addEventListener('timeupdate',maj);v.addEventListener('seeked',maj);
const pas=d=>{v.pause();v.currentTime=Math.min(D,Math.max(0,v.currentTime+d/FPS))};
document.querySelectorAll('[data-d]').forEach(b=>b.onclick=()=>pas(+b.dataset.d));
$('lec').onclick=()=>v.paused?v.play():v.pause();
document.addEventListener('keydown',e=>{if(e.target.tagName==='TEXTAREA')return;
 if(e.key===' '){e.preventDefault();$('lec').click()}else if(e.key==='ArrowLeft'){e.preventDefault();pas(-1)}else if(e.key==='ArrowRight'){e.preventDefault();pas(1)}
 else if(e.key.toLowerCase()==='n'){e.preventDefault();v.pause();$('txt').focus()}});
function dessine(){fr.querySelectorAll('.note').forEach(x=>x.remove());
 $('notes').innerHTML=N.map((n,i)=>`<li><a data-t="${n.t}">${fmt(n.t)}</a><span>${esc(n.texte)}</span><button data-i="${i}">×</button></li>`).join('')||'<li style="color:var(--g)">Aucune note pour l’instant.</li>';
 for(const n of N){const d=document.createElement('div');d.className='note';d.style.left=n.t/D*100+'%';fr.appendChild(d)}
 document.querySelectorAll('#notes a').forEach(a=>a.onclick=()=>{v.currentTime=+a.dataset.t});
 document.querySelectorAll('#notes button').forEach(b=>b.onclick=()=>{N.splice(+b.dataset.i,1);enr()})}
async function enr(){dessine();try{const r=await fetch('/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(N)});
 $('etat').textContent=r.ok?'Enregistré. Dis « corrige avec mes notes » à Claude.':'Pas enregistré ('+r.status+')'}catch(e){$('etat').textContent='Pas enregistré : le serveur est arrêté.'}}
$('aj').onclick=()=>{const x=$('txt').value.trim();if(!x)return;const t=Math.round(v.currentTime*1000)/1000;
 const ici=P.filter(p=>p.de<=t&&t<p.a).map(p=>p.piste+' : '+p.texte);N.push({t,texte:x,ici});N.sort((a,b)=>a.t-b.t);$('txt').value='';enr()};
dessine();maj();
</script></body></html>"""


def js(x):
    """JSON sûr dans un <script> en ligne : un texte « </script> » ne peut pas fermer la balise."""
    return json.dumps(x, ensure_ascii=False).replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")


def serveur(video, notes, page, port=None):
    def hotes(h):
        p = h.server.server_address[1] if port is None else port
        return {f"127.0.0.1:{p}", f"localhost:{p}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def envoie(self, code, corps, typ, extra=()):
            self.send_response(code)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(corps)))
            for k, val in extra:
                self.send_header(k, val)
            self.end_headers()
            self.wfile.write(corps)

        def do_GET(self):
            if self.headers.get("Host") not in hotes(self):  # bloque le « DNS rebinding »
                return self.envoie(403, b"", "text/plain")
            if self.path == "/":
                return self.envoie(200, page, "text/html; charset=utf-8")
            if self.path != "/video":
                return self.envoie(404, b"", "text/plain")
            taille = video.stat().st_size
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            de, a = (int(m[1] or 0), min(int(m[2]) if m[2] else taille - 1, taille - 1)) if m else (0, taille - 1)
            with video.open("rb") as f:  # le navigateur demande des morceaux (Range) pour pouvoir avancer image par image
                f.seek(de)
                corps = f.read(a - de + 1)
            self.envoie(206 if m else 200, corps, "video/mp4",
                        [("Accept-Ranges", "bytes")] + ([("Content-Range", f"bytes {de}-{a}/{taille}")] if m else []))

        def do_POST(self):
            if self.path != "/notes":
                return self.envoie(404, b"", "text/plain")
            h = self.headers
            # un autre site ouvert dans le navigateur ne doit pas pouvoir écrire les notes
            if (h.get("Host") not in hotes(self) or h.get("Origin") not in (None, *(f"http://{x}" for x in hotes(self)))
                    or h.get("Content-Type", "").split(";")[0].strip() != "application/json"):
                return self.envoie(403, b"", "text/plain")
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                assert isinstance(data, list) and all(isinstance(n, dict) and isinstance(n.get("t"), (int, float))
                                                  and isinstance(n.get("texte"), str) for n in data)
            except (ValueError, AssertionError):
                return self.envoie(400, b"notes invalides", "text/plain")
            notes.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            self.envoie(200, b"ok", "text/plain")
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--root", default=".")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--notes", action="store_true", help="affiche les notes enregistrées et quitte")
    ap.add_argument("--partition", action="store_true",
                    help="écrit la frise en texte (qui est à l'écran, quand, et les blancs sans parole ≥ 0,15 s) et quitte")
    ap.add_argument("--sans-navigateur", action="store_true")
    a = ap.parse_args(argv)
    root, video = Path(a.root), Path(a.video)
    if not video.exists():
        die(f"vidéo introuvable : {video}")
    if a.partition:
        f = frise(video, root)
        rendu = root / ".monteur" / "edl.rendu.json"
        edl = json.loads(rendu.read_text(encoding="utf-8")) if rendu.exists() else {}
        if Path(edl.get("output", "")).name == video.name:  # blancs mesurés sur les mots gardés (les sous-titres s'étirent)
            import check
            ws = check.output_words(edl, root)[0]
            f += [{"piste": "blanc", "de": x["end"], "a": y["start"], "texte": f"{y['start'] - x['end']:.2f} s sans parole"}
                  for x, y in zip(ws, ws[1:]) if y["start"] - x["end"] >= 0.15]
        f.sort(key=lambda x: (x["de"], x["piste"]))
        for x in f:
            print(f"{x['de']:6.2f}-{x['a']:6.2f}  {x['piste']:<10} {x['texte']}")
        return f
    notes = root / ".monteur" / "review" / f"{video.stem}.json"
    existantes = json.loads(notes.read_text(encoding="utf-8")) if notes.exists() else []
    if a.notes:
        if not existantes:
            print(f"Aucune note pour {video.name}.")
        for n in existantes:
            print(f"- {n['t']:.2f} s : {n['texte']}" + (f"   [à l'image : {' | '.join(n['ici'])}]" if n.get("ici") else ""))
        return existantes
    notes.parent.mkdir(parents=True, exist_ok=True)
    info = probe(video)
    page = (PAGE.replace("__PISTES__", js(frise(video, root)))
            .replace("__FPS__", str(info.get("fps") or 30)).replace("__DUREE__", str(info["duration"]))
            .replace("__NOTES__", js(existantes))).encode("utf-8")
    url = f"http://127.0.0.1:{a.port}"
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), serveur(video.resolve(), notes, page))
    print(f"Review : {url}  (notes → {notes}) — Ctrl+C pour arrêter")
    if not a.sans_navigateur:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

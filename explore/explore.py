#!/usr/bin/env python3
"""Etape 2 - Exploration des fichiers open data "Prix des carburants".

Aucune dependance externe (bibliotheque standard, Python 3.12).

Sous-commandes :
  head      mesurer taille et type des URL officielles SANS rien telecharger
  download  telecharger un fichier (garde-fou de taille, sha256, manifest)
  inspect   bilan de qualite d'un ZIP (lecture en flux, memoire faible)
  compare   comparer deux ZIP (ex. deux jours consecutifs)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from array import array
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://donnees.roulez-eco.fr/opendata"
UA = {"User-Agent": "fuel-observatory-portfolio/0.1 (projet d'apprentissage)"}
METRO_LAT, METRO_LON = (41.0, 51.5), (-5.5, 10.0)  # boite englobante metropole + Corse


# ----------------------------------------------------------------- reseau --
def head(url: str) -> tuple[int | None, dict]:
    """Statut + en-tetes, sans lire le corps. HEAD d'abord, GET (non lu) sinon."""
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405, 501):
                continue
            return e.code, dict(e.headers)
        except Exception as e:  # reseau, DNS, TLS...
            return None, {"error": f"{type(e).__name__}: {e}"}
    return None, {"error": "HEAD et GET refuses"}


def cmd_head(a) -> int:
    today = datetime.now().date()
    urls = a.urls or (
        [f"{BASE}/{p}" for p in ("instantane", "instantane_ruptures", "jour", "annee")]
        + [f"{BASE}/annee/{y}" for y in (2024, 2025, today.year)]
        + [f"{BASE}/jour/{today - timedelta(days=d):%Y%m%d}" for d in (1, 2)]
    )
    print(f"{'statut':<7}{'taille':>10}  {'type':<28}{'last-modified':<31}url")
    for u in urls:
        status, h = head(u)
        h = {k.lower(): v for k, v in h.items()}
        size = f"{int(h['content-length']) / 1e6:.1f} Mo" if h.get("content-length") else "?"
        info = h.get("error", "")
        print(f"{str(status):<7}{size:>10}  {h.get('content-type', '-')[:27]:<28}"
              f"{h.get('last-modified', '-'):<31}{u} {info}")
    return 0


def cmd_download(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    status, h = head(a.url)
    if status != 200:
        print(f"Refus : statut {status} {h.get('error', '')}")
        return 1
    h = {k.lower(): v for k, v in h.items()}
    limit = int(a.max_mb * 1e6)
    declared = int(h.get("content-length") or 0)
    if declared > limit and not a.force:
        print(f"Refus : {declared / 1e6:.1f} Mo > limite {a.max_mb} Mo (utilise --force)")
        return 2
    label = re.sub(r"[^A-Za-z0-9_-]+", "_", a.url.removeprefix(BASE + "/")).strip("_") or "fichier"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = out / f"{label}_{stamp}.zip"
    part = final.with_suffix(".zip.part")  # ecriture partielle puis renommage
    sha, total = hashlib.sha256(), 0
    req = urllib.request.Request(a.url, headers=UA)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 20):
            total += len(chunk)
            if total > limit and not a.force:
                part.unlink(missing_ok=True)
                print(f"Abandon : depasse {a.max_mb} Mo (taille non annoncee)")
                return 2
            sha.update(chunk)
            f.write(chunk)
    part.replace(final)
    entry = {"url": a.url, "file": final.name, "bytes": total, "sha256": sha.hexdigest(),
             "downloaded_at_utc": stamp, "http_last_modified": h.get("last-modified")}
    with open(out / "manifest.jsonl", "a", encoding="utf-8") as m:
        m.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"OK {final}  {total / 1e6:.2f} Mo en {time.time() - t0:.1f}s  sha256={sha.hexdigest()[:16]}...")
    if not zipfile.is_zipfile(final):
        print("ATTENTION : ce fichier n'est pas un ZIP valide.")
    return 0


# ------------------------------------------------------------- lecture XML --
def open_xml(zpath: str):
    zf = zipfile.ZipFile(zpath)
    names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
    if not names:
        raise SystemExit(f"Aucun .xml dans {zpath} : {zf.namelist()}")
    return zf, names[0]


def iter_pdv(zpath: str):
    """Genere chaque <pdv> en flux, puis le libere (memoire constante)."""
    zf, name = open_xml(zpath)
    with zf, zf.open(name) as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == "pdv":
                yield el
                el.clear()


def parse_maj(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime((s or "")[:19], fmt)
        except ValueError:
            pass
    return None


def parse_price(s):
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def pct(sorted_vals, q):
    return sorted_vals[int(q * (len(sorted_vals) - 1))]


# ----------------------------------------------------------------- inspect --
def cmd_inspect(a) -> int:
    zf, name = open_xml(a.zip)
    print(f"== {a.zip}")
    for i in zf.infolist():
        print(f"   membre {i.filename}: {i.file_size / 1e6:.1f} Mo (compresse {i.compress_size / 1e6:.1f} Mo)")
    with zf.open(name) as f:
        print("   debut du fichier :", f.read(120).decode("latin-1", "replace").splitlines()[0])
    zf.close()

    now = datetime.now()
    ids, schema, pop = Counter(), Counter(), Counter()
    fuels, months = Counter(), Counter()
    prices: dict[str, array] = {}
    price_samples: dict[str, list] = {}
    seen_events: set[int] = set()  # hash 64 bits du triplet : evite de stocker des millions de tuples
    lat, lon = array("d"), array("d")
    c = Counter()
    bad_cp, bad_price_samples = [], []
    rupture_types, fermeture_types = Counter(), Counter()
    maj_min = maj_max = None

    for pdv in iter_pdv(a.zip):
        c["pdv"] += 1
        pid = pdv.get("id")
        ids[pid] += 1
        for el in pdv.iter():
            schema[f"{el.tag}[{','.join(sorted(el.attrib))}]"] += 1
        pop[pdv.get("pop")] += 1
        cp = pdv.get("cp")
        if not (cp and len(cp) == 5 and cp.isdigit()):
            c["cp_invalide"] += 1
            if len(bad_cp) < 5:
                bad_cp.append(cp)
        la, lo = parse_price(pdv.get("latitude")), parse_price(pdv.get("longitude"))
        if la is None or lo is None or la == 0 or lo == 0:
            c["coord_absente_ou_zero"] += 1
        else:
            lat.append(la)
            lon.append(lo)
        plist = pdv.findall("prix")
        if not plist:
            c["pdv_sans_prix"] += 1
        for p in plist:
            nom = p.get("nom")
            fuels[nom] += 1
            raw = p.get("valeur")
            v = parse_price(raw)
            if len(price_samples.setdefault(nom, [])) < 3:
                price_samples[nom].append(raw)
            if v is None or v <= 0 or v > 10:
                c["prix_invalide_ou_hors_0_10"] += 1
                if len(bad_price_samples) < 5:
                    bad_price_samples.append((pid, nom, raw))
            else:
                prices.setdefault(nom, array("d")).append(v)
            m = parse_maj(p.get("maj"))
            if m is None:
                c["maj_illisible"] += 1
            else:
                maj_min = m if maj_min is None else min(maj_min, m)
                maj_max = m if maj_max is None else max(maj_max, m)
                months[m.strftime("%Y-%m")] += 1
                if m > now + timedelta(days=1):
                    c["maj_dans_le_futur"] += 1
            key = hash(f"{pid}|{nom}|{p.get('maj')}")
            if key in seen_events:
                c["evenement_prix_en_double"] += 1
            seen_events.add(key)
            c["prix"] += 1
        for r in pdv.findall("rupture"):
            rupture_types[r.get("type")] += 1
        for fe in pdv.findall("fermeture"):
            fermeture_types[fe.get("type")] += 1

    dup_ids = {k: v for k, v in ids.items() if v > 1}
    print(f"\n-- Volumes : {c['pdv']} noeuds pdv, {len(ids)} id distincts, "
          f"{len(dup_ids)} id repetes, {c['prix']} noeuds prix")
    print("-- Schema reel (balise[attributs] : occurrences) :")
    for k, v in sorted(schema.items()):
        print(f"     {k:<60}{v}")
    print(f"-- pop (A=autoroute, R=route) : {dict(pop)}")
    print(f"-- Codes postaux invalides : {c['cp_invalide']}  exemples={bad_cp}")

    if lat:
        scale = 100000 if max(abs(x) for x in lat) > 1000 else 1
        outside = sum(1 for x, y in zip(lat, lon)
                      if not (METRO_LAT[0] <= x / scale <= METRO_LAT[1]
                              and METRO_LON[0] <= y / scale <= METRO_LON[1]))
        print(f"-- Coordonnees brutes : lat [{min(lat):.0f} ; {max(lat):.0f}]  lon [{min(lon):.0f} ; {max(lon):.0f}]"
              f"  -> unite deduite : {'x100000 (documente)' if scale == 100000 else 'degres ?'}")
        print(f"   absentes/zero : {c['coord_absente_ou_zero']}   hors boite metropole+Corse : {outside}"
              " (DOM attendus + erreurs eventuelles)")

    print("-- Prix par carburant (n, min, p1, mediane, p99, max) et format brut :")
    for nom in sorted(prices, key=str):
        s = sorted(prices[nom])
        print(f"     {str(nom):<8} n={len(s):<7} min={s[0]:.3f} p1={pct(s, .01):.3f} "
              f"med={pct(s, .5):.3f} p99={pct(s, .99):.3f} max={s[-1]:.3f}  exemples bruts={price_samples[nom]}")
    print(f"   prix invalides ou hors ]0;10] : {c['prix_invalide_ou_hors_0_10']}  exemples={bad_price_samples}")
    print(f"-- Dates maj : min={maj_min} max={maj_max} illisibles={c['maj_illisible']} futur={c['maj_dans_le_futur']}")
    print("   nombre d'evenements de prix par mois de maj (la reponse a 'le stock annuel contient-il tout l'historique ?') :")
    for mth in sorted(months)[-60:]:
        print(f"     {mth}  {months[mth]}")
    print(f"-- Evenements de prix en double (meme id, carburant, maj) : {c['evenement_prix_en_double']}")
    print(f"-- Stations sans aucun prix : {c['pdv_sans_prix']}")
    print(f"-- Ruptures par type : {dict(rupture_types)}   Fermetures par type : {dict(fermeture_types)}")
    if dup_ids:
        print(f"   exemples d'id repetes : {list(dup_ids.items())[:5]}")

    if a.json:
        report = {"zip": a.zip, "pdv": c["pdv"], "ids_distincts": len(ids), "prix": c["prix"],
                  "compteurs": dict(c), "carburants": dict(fuels), "mois": dict(sorted(months.items()))}
        Path(a.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nBilan ecrit dans {a.json}")
    return 0


# ----------------------------------------------------------------- compare --
def load_events(zpath: str):
    ids, ev = set(), {}
    for pdv in iter_pdv(zpath):
        pid = pdv.get("id")
        ids.add(pid)
        for p in pdv.findall("prix"):
            ev[(pid, p.get("nom"), p.get("maj"))] = p.get("valeur")
    return ids, ev


def cmd_compare(a) -> int:
    for z in (a.zip_a, a.zip_b):
        if Path(z).stat().st_size > 60e6 and not a.force:
            print(f"Refus : {z} depasse 60 Mo (compare est prevu pour des fichiers 'jour'). Utilise --force.")
            return 2
    ids_a, ev_a = load_events(a.zip_a)
    ids_b, ev_b = load_events(a.zip_b)
    both = ev_a.keys() & ev_b.keys()
    changed = sum(1 for k in both if ev_a[k] != ev_b[k])
    union = len(ev_a.keys() | ev_b.keys())
    print(f"A = {a.zip_a}\nB = {a.zip_b}")
    print(f"Stations : A={len(ids_a)} B={len(ids_b)} communes={len(ids_a & ids_b)} "
          f"seulement A={len(ids_a - ids_b)} seulement B={len(ids_b - ids_a)}")
    print(f"Evenements de prix (id, carburant, maj) : A={len(ev_a)} B={len(ev_b)} "
          f"communs={len(both)} seulement A={len(ev_a) - len(both)} seulement B={len(ev_b) - len(both)}")
    print(f"Recouvrement (communs / union) = {len(both) / union:.1%}" if union else "Aucun evenement")
    print(f"Evenements communs dont la valeur differe : {changed}")
    for label, ev in (("A", ev_a), ("B", ev_b)):
        days = Counter((k[2] or "")[:10] for k in ev)
        print(f"Dates de maj les plus frequentes dans {label} : {days.most_common(5)} (sur {len(days)} dates distinctes)")
    print("Lecture : un recouvrement eleve avec des maj etalees sur beaucoup de dates suggere un fichier ETAT COMPLET ;\n"
          "          peu d'evenements concentres sur une date suggere un fichier de CHANGEMENTS du jour. A confirmer avec le bilan 'inspect'.")
    return 0


# --------------------------------------------------------------------- CLI --
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # consoles Windows
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("head", help="taille/type des URL sans telecharger")
    p.add_argument("urls", nargs="*")
    p.set_defaults(fn=cmd_head)
    p = sub.add_parser("download", help="telecharger avec garde-fou")
    p.add_argument("url")
    p.add_argument("--out", required=True)
    p.add_argument("--max-mb", type=float, default=60)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_download)
    p = sub.add_parser("inspect", help="bilan de qualite d'un ZIP")
    p.add_argument("zip")
    p.add_argument("--json")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("compare", help="comparer deux ZIP")
    p.add_argument("zip_a")
    p.add_argument("zip_b")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_compare)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
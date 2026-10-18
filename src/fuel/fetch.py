"""Téléchargement des fichiers officiels + archivage des données brutes.

- Le fichier brut est gardé tel quel dans data/raw (jamais supprimé), avec la date dans le nom.
- Un fichier incomplet ou qui n'est pas un ZIP valide n'est jamais archivé.
- Pannes réseau et erreurs serveur (5xx) : on réessaie avec une attente qui double.
  Erreur 4xx (ex. 404) : inutile de réessayer, on s'arrête tout de suite.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import socket
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger("fuel.fetch")

BASE_URL = "https://donnees.roulez-eco.fr/opendata"
RAW_DIR = Path("data/raw")


class FetchError(Exception):
    pass


def url_for(year: int | None = None) -> tuple[str, str]:
    """(url, nom court). Sans année : le fichier de l'année en cours (mis à jour chaque jour)."""
    if year is None:
        return f"{BASE_URL}/annee", "annee_en_cours"
    return f"{BASE_URL}/annee/{year}", f"annee_{year}"


def _download_once(url: str, part: Path, timeout: float, max_bytes: int) -> str:
    """Télécharge dans `part`, renvoie le sha256. Lève une erreur si incomplet ou trop gros."""
    req = urllib.request.Request(url, headers={"User-Agent": "fuel-observatory/0.1 (projet portfolio)"})
    h = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as out:
        expected = resp.headers.get("Content-Length")
        while chunk := resp.read(1 << 20):
            size += len(chunk)
            if size > max_bytes:
                raise FetchError(f"fichier plus gros que la limite ({max_bytes} octets)")
            h.update(chunk)
            out.write(chunk)
    if expected is not None and size != int(expected):
        raise http.client.IncompleteRead(b"", int(expected) - size)
    return h.hexdigest()


def _check_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                raise FetchError("ZIP corrompu")
            if not any(n.lower().endswith(".xml") for n in z.namelist()):
                raise FetchError("le ZIP ne contient aucun fichier .xml")
    except zipfile.BadZipFile as e:
        raise FetchError(f"ce n'est pas un ZIP valide ({e})") from e

def _known_archive(raw_dir: Path, digest: str) -> Path | None:
    """Un fichier de même contenu (même sha256) est-il déjà archivé ?"""
    manifest = raw_dir / "manifest.jsonl"
    if not manifest.exists():
        return None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["sha256"] == digest and (raw_dir / entry["file"]).exists():
            return raw_dir / entry["file"]
    return None

def download(
    url: str,
    label: str,
    raw_dir: Path = RAW_DIR,
    retries: int = 4,
    backoff: float = 5.0,
    timeout: float = 60.0,
    max_mb: int = 500,
) -> Path:
    """Télécharge `url`, vérifie, archive dans raw_dir. Renvoie le chemin du fichier archivé."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    final = raw_dir / f"{label}_{stamp}.zip"
    part = raw_dir / f"{label}_{stamp}.zip.part"

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            log.info("téléchargement %s (essai %d/%d)", url, attempt, retries)
            digest = _download_once(url, part, timeout, max_mb * 1024 * 1024)
            _check_zip(part)
            known = _known_archive(raw_dir, digest)
            if known is not None:  # contenu identique déjà archivé : inutile d'en garder une copie
                part.unlink()
                log.info("contenu identique à %s : pas de nouvelle copie", known.name)
                return known
            part.replace(final)
            size = final.stat().st_size
            with open(raw_dir / "manifest.jsonl", "a", encoding="utf-8") as m:
                m.write(json.dumps({
                    "file": final.name, "url": url, "sha256": digest, "size_bytes": size,
                    "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                }) + "\n")
            log.info("archivé : %s (%.1f Mo)", final, size / 1e6)
            return final
        except urllib.error.HTTPError as e:
            if e.code < 500:  # 404, 403... : réessayer ne servirait à rien
                part.unlink(missing_ok=True)
                raise FetchError(f"le serveur répond {e.code} pour {url}") from e
            last_error = e
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                http.client.HTTPException, FetchError) as e:
            last_error = e
        part.unlink(missing_ok=True)
        log.warning("échec : %s", last_error)
        if attempt < retries:
            time.sleep(backoff * 2 ** (attempt - 1))
    raise FetchError(f"abandon après {retries} essais : {last_error}")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)  # les logs sur stderr, le chemin seul sur stdout
    year = int(sys.argv[1]) if len(sys.argv) > 1 else None
    url, label = url_for(year)
    print(download(url, label))
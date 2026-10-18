"""Tests du téléchargeur avec un faux serveur local (aucun accès à internet)."""
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fuel.fetch import FetchError, download, url_for


def good_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data.xml", "<pdv_liste/>")
    return buf.getvalue()


@pytest.fixture
def server():
    """Faux serveur. `plan` = liste de réponses données une par une : (code, contenu)."""
    state = {"plan": [], "hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"] += 1
            code, body = state["plan"].pop(0) if state["plan"] else (200, good_zip())
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}/f"
    yield state
    httpd.shutdown()


def test_url_for():
    assert url_for(2025) == ("https://donnees.roulez-eco.fr/opendata/annee/2025", "annee_2025")
    assert url_for() == ("https://donnees.roulez-eco.fr/opendata/annee", "annee_en_cours")


def test_download_archives_file_and_manifest(server, tmp_path):
    path = download(server["url"], "annee_2025", raw_dir=tmp_path, backoff=0)
    assert path.exists() and path.name.startswith("annee_2025_") and path.suffix == ".zip"
    line = json.loads((tmp_path / "manifest.jsonl").read_text().splitlines()[0])
    assert line["file"] == path.name and len(line["sha256"]) == 64
    assert not list(tmp_path.glob("*.part"))


def test_retries_after_server_errors(server, tmp_path):
    server["plan"] = [(503, b"panne"), (500, b"panne")]  # puis OK
    path = download(server["url"], "x", raw_dir=tmp_path, backoff=0)
    assert path.exists()
    assert server["hits"] == 3


def test_404_is_not_retried(server, tmp_path):
    server["plan"] = [(404, b"introuvable")]
    with pytest.raises(FetchError, match="404"):
        download(server["url"], "x", raw_dir=tmp_path, backoff=0)
    assert server["hits"] == 1
    assert not list(tmp_path.glob("x_*"))  # rien d'archivé


def test_not_a_zip_is_never_archived(server, tmp_path):
    server["plan"] = [(200, b"<html>page d'erreur</html>")] * 3
    with pytest.raises(FetchError, match="abandon"):
        download(server["url"], "x", raw_dir=tmp_path, retries=3, backoff=0)
    assert server["hits"] == 3
    assert not list(tmp_path.glob("x_*"))


def test_too_big_is_refused(server, tmp_path):
    server["plan"] = [(200, b"0" * 2_000_000)]
    with pytest.raises(FetchError, match="abandon|limite"):
        download(server["url"], "x", raw_dir=tmp_path, retries=1, backoff=0, max_mb=1)
    assert not list(tmp_path.glob("x_*"))


def test_unreachable_server_gives_clear_error(tmp_path):
    with pytest.raises(FetchError, match="abandon"):
        download("http://127.0.0.1:9/f", "x", raw_dir=tmp_path, retries=2, backoff=0, timeout=2)

def test_identical_content_is_not_archived_twice(server, tmp_path):
    first = download(server["url"], "annee_en_cours", raw_dir=tmp_path, backoff=0)
    second = download(server["url"], "annee_en_cours", raw_dir=tmp_path, backoff=0)
    assert second == first
    assert len(list(tmp_path.glob("*.zip"))) == 1
    assert len(
        (tmp_path / "manifest.jsonl").read_text().splitlines()
    ) == 1
#!/usr/bin/env python3
"""SANS Index Builder - a small local tool for building a SANS/GIAC course index.

    python3 sans_index.py                 # start and open the browser
    python3 sans_index.py --port 9000 --no-browser --data /path/to/folder

Standard library only. Indexes are stored as JSON in ./data/, with rolling
snapshots in ./data/backups/<index>/ and deleted indexes moved to ./data/trash/.

The data folder can live in iCloud Drive / OneDrive / Dropbox and be used from
several computers: every save merges entry by entry (newest edit wins, deletions
are remembered), and conflict copies made by the sync service are merged back in.
"""

import argparse
import io
import json
import os
import re
import shutil
import socket
import threading
import time
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent
APP_HTML = ROOT / "app.html"
DATA_DIR = ROOT / "data"
BACKUP_DIR = DATA_DIR / "backups"
TRASH_DIR = DATA_DIR / "trash"

BACKUP_INTERVAL = 10 * 60  # seconds between snapshots of the same index
BACKUPS_KEPT = 30          # snapshots kept per index
MAX_BODY = 50 * 1024 * 1024

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

write_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- storage

META_KEYS = ("title", "books", "settings", "cursor")  # whole-value fields, newest stamp wins
HOST = re.sub(r"[^A-Za-z0-9-]", "", socket.gethostname().split(".")[0])[:24] or "pc"


def index_path(name):
    if not NAME_RE.match(name or ""):
        raise ApiError(400, "Invalid index name: use letters, numbers, spaces, _ . - (max 64).")
    return DATA_DIR / f"{name}.json"


def read_json(path, attempts=4):
    # A sync client or antivirus may briefly lock or be rewriting the file.
    for attempt in range(attempts):
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            if attempt == attempts - 1:
                raise
            time.sleep(0.15)


def write_json(path, doc):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")
    for _ in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: target briefly locked by a sync client
            time.sleep(0.2)
    os.replace(tmp, path)


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _dict(value):
    return value if isinstance(value, dict) else {}


def _mt(entry):
    return _num(entry.get("mt") or entry.get("ts"))


def merge_docs(base, other):
    """Combine two copies of an index: the newest edit of each entry wins, deletions included."""
    deleted = dict(_dict(base.get("deleted")))
    for key, t in _dict(other.get("deleted")).items():
        if _num(t) > _num(deleted.get(key)):
            deleted[key] = t
    entries = {}
    for e in list(base.get("entries") or []) + list(other.get("entries") or []):
        if isinstance(e, dict) and e.get("id") and (e["id"] not in entries or _mt(e) > _mt(entries[e["id"]])):
            entries[e["id"]] = e
    merged = dict(base)
    merged["entries"] = [e for e in entries.values()
                         if not (e["id"] in deleted and _num(deleted[e["id"]]) >= _mt(e))]
    merged["deleted"] = deleted
    stamps, other_stamps = dict(_dict(base.get("stamps"))), _dict(other.get("stamps"))
    for key in META_KEYS:
        if key in other and (key not in base or _num(other_stamps.get(key)) > _num(stamps.get(key))):
            merged[key] = other[key]
            stamps[key] = other_stamps.get(key, 0)
    merged["stamps"] = stamps
    return merged


def conflict_copies(name):
    """Duplicates a sync service made ('SEC504 2.json', 'SEC504-DESKTOP-1.json',
    'SEC504 (conflicted copy).json'), recognised by the index name stored inside."""
    found = []
    for path in DATA_DIR.glob("*.json"):
        if path.stem == name or not path.stem.startswith(name):
            continue
        try:
            doc = read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("name") == name:
            found.append((path, doc))
    return found


def retire_copies(name, paths):
    if not paths:
        return
    folder = BACKUP_DIR / name / "conflicts"
    folder.mkdir(parents=True, exist_ok=True)
    for path in paths:
        os.replace(path, folder / f"{datetime.now():%Y%m%d-%H%M%S}-{HOST}-{path.name}")


def commit(name, path, before, after):
    """Write `after` unless it is identical to what is on disk (`before`)."""
    after.update(name=name, version=2)
    after.pop("rev", None)
    after.pop("savedAt", None)
    if before is not None and {k: v for k, v in before.items() if k != "savedAt"} == after:
        return
    if path.exists():
        snapshot(name, path)
    after["savedAt"] = datetime.now().isoformat(timespec="seconds")
    write_json(path, after)


def sync_index(name, incoming=None):
    """Merge disk, any sync-conflict copies and (optionally) the browser's copy; save if changed."""
    path = index_path(name)
    with write_lock:
        disk = read_json(path) if path.exists() else None
        doc = disk
        copies = conflict_copies(name)
        for _, other in copies:
            doc = merge_docs(doc, other) if doc else other
        if incoming is not None:
            doc = merge_docs(doc or {}, incoming)
        if doc is None:
            raise ApiError(404, f"No index named {name!r}")
        if doc is not disk:
            commit(name, path, disk, doc)
        retire_copies(name, [p for p, _ in copies])
    return doc


def list_indexes():
    names = set()
    for path in DATA_DIR.glob("*.json"):
        try:
            inner = read_json(path).get("name")
        except (OSError, ValueError, AttributeError):
            inner = None
        name = inner if isinstance(inner, str) and NAME_RE.match(inner) else path.stem
        names.add(name)  # a conflict copy lists under the index it belongs to
    return [{"name": n} for n in sorted(names, key=str.lower)]


def snapshot(name, path):
    """Copy the current file into backups/, at most once per BACKUP_INTERVAL."""
    folder = BACKUP_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    existing = sorted(folder.glob("*.json"))
    if existing and time.time() - existing[-1].stat().st_mtime < BACKUP_INTERVAL:
        return
    shutil.copyfile(path, folder / f"{datetime.now():%Y%m%d-%H%M%S}-{HOST}.json")
    for old in sorted(folder.glob("*.json"))[:-BACKUPS_KEPT]:
        old.unlink()


def trash_index(name):
    path = index_path(name)
    if not path.exists():
        raise ApiError(404, f"No index named {name!r}")
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    with write_lock:
        os.replace(path, TRASH_DIR / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.json")


# --------------------------------------------------------------------------- docx

PAPER = {"letter": (12240, 15840), "a4": (11906, 16838)}  # twentieths of a point
MARGIN = 720                                               # 0.5 inch
HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")
BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
BORDERS = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                  for side in ("top", "left", "bottom", "right", "insideH", "insideV"))

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.styles+xml"/>'
    '</Types>')
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/styles" Target="styles.xml"/></Relationships>')
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles {W_NS}><w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
    '</w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="18"/></w:rPr></w:style>'
    '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
    '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/></w:tblPr></w:style></w:styles>')


def _fill(value):
    m = HEX_RE.match(str(value or ""))
    return m.group(1).upper() if m else None


def _run(text, bold=False, size=None, color=None):
    props = ("<w:b/>" if bold else "") + (f'<w:color w:val="{color}"/>' if color else "")
    if size:
        props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    lines = BAD_XML.sub("", str(text if text is not None else "")).split("\n")
    body = "<w:br/>".join(f'<w:t xml:space="preserve">{xml_escape(line)}</w:t>' for line in lines)
    return f"<w:r>{'<w:rPr>' + props + '</w:rPr>' if props else ''}{body}</w:r>"


def _para(runs="", keep_next=False, space_after=0):
    ppr = ("<w:keepNext/>" if keep_next else "") + \
          (f'<w:spacing w:after="{space_after}"/>' if space_after else "")
    return f"<w:p>{'<w:pPr>' + ppr + '</w:pPr>' if ppr else ''}{runs}</w:p>"


def _cell(width, content, fill=None, span=1):
    props = f'<w:tcW w:w="{width}" w:type="dxa"/>'
    if span > 1:
        props += f'<w:gridSpan w:val="{span}"/>'
    if fill:
        props += f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
    return f"<w:tc><w:tcPr>{props}</w:tcPr>{content}</w:tc>"


def _row(cells, header=False):
    return f"<w:tr><w:trPr><w:cantSplit/>{'<w:tblHeader/>' if header else ''}</w:trPr>{cells}</w:tr>"


def _table(widths, rows):
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    return ("<w:tbl><w:tblPr>"
            f'<w:tblW w:w="{sum(widths)}" w:type="dxa"/>'
            f"<w:tblBorders>{BORDERS}</w:tblBorders>"
            '<w:tblLayout w:type="fixed"/>'
            '<w:tblCellMar><w:top w:w="15" w:type="dxa"/><w:left w:w="60" w:type="dxa"/>'
            '<w:bottom w:w="15" w:type="dxa"/><w:right w:w="60" w:type="dxa"/></w:tblCellMar>'
            f"</w:tblPr><w:tblGrid>{grid}</w:tblGrid>{''.join(rows)}</w:tbl>")


def build_docx(model):
    """Render the export model built by the browser (title, columns, groups of rows) to .docx."""
    page_w, page_h = PAPER.get(model.get("paper"), PAPER["letter"])
    content_w = page_w - 2 * MARGIN
    columns = model.get("columns") or []
    if not columns:
        raise ApiError(400, "Export has no columns")
    total = sum(float(c.get("width") or 1) for c in columns)
    widths = [int(content_w * float(c.get("width") or 1) / total) for c in columns]
    ncol = len(columns)

    body = _para(_run(model.get("title") or "Index", bold=True, size=32), space_after=40)
    if model.get("subtitle"):
        body += _para(_run(model["subtitle"], size=16, color="666666"), space_after=120)

    legend = model.get("legend") or []
    if legend:
        lw = content_w // len(legend)
        cells = "".join(_cell(lw, _para(_run(item.get("label", ""), size=16)), fill=_fill(item.get("fill")))
                        for item in legend)
        body += _table([lw] * len(legend), [_row(cells)]) + _para(space_after=120)

    rows = [_row("".join(_cell(w, _para(_run(c.get("label", ""), bold=True)), fill="D9DCE3")
                         for w, c in zip(widths, columns)), header=True)]
    for group in model.get("groups") or []:
        label = _para(_run(group.get("label", ""), bold=True, size=22, color="FFFFFF"), keep_next=True)
        rows.append(_row(_cell(sum(widths), label, fill="3A3F4B", span=ncol)))
        for row in group.get("rows") or []:
            values = list(row.get("cells") or []) + [""] * ncol
            fill = _fill(row.get("fill"))
            rows.append(_row("".join(_cell(w, _para(_run(values[i], bold=(i == 0))), fill=fill)
                                     for i, w in enumerate(widths))))
    body += _table(widths, rows)
    body += (f'<w:p/><w:sectPr><w:pgSz w:w="{page_w}" w:h="{page_h}"/>'
             f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" w:left="{MARGIN}" '
             'w:header="360" w:footer="360" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f"<w:document {W_NS}><w:body>{body}</w:body></w:document>")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


# --------------------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "SANSIndex/1.0"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        try:
            self._check_request(method)
            parts = [unquote(p) for p in urlparse(self.path).path.split("/") if p]
            status, body, ctype, extra = self._route(method, parts)
        except ApiError as e:
            status, body, ctype, extra = e.status, {"error": str(e)}, None, {}
        except Exception as e:  # keep the server alive and tell the browser why
            status, body, ctype, extra = 500, {"error": f"{type(e).__name__}: {e}"}, None, {}
        self._send(status, body, ctype, extra)

    def _check_request(self, method):
        # Only answer requests addressed to this local server from its own page
        # (blocks DNS-rebinding and cross-site requests from other websites).
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            raise ApiError(403, "Forbidden host")
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] not in allowed:
            raise ApiError(403, "Forbidden origin")
        if method in ("PUT", "POST") and "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ApiError(415, "Expected a JSON body")

    def _route(self, method, parts):
        if method == "GET" and parts in ([], ["index.html"]):
            return 200, APP_HTML.read_bytes(), "text/html; charset=utf-8", {}
        if parts[:2] == ["api", "indexes"]:
            if len(parts) == 2 and method == "GET":
                return 200, {"indexes": list_indexes()}, None, {}
            if len(parts) == 3:
                name = parts[2]
                if method == "GET":
                    return 200, sync_index(name), None, {}
                if method == "PUT":
                    incoming = self._json()
                    if not isinstance(incoming, dict) or not isinstance(incoming.get("entries"), list):
                        raise ApiError(400, "Malformed index document")
                    return 200, sync_index(name, incoming), None, {}
                if method == "DELETE":
                    trash_index(name)
                    return 200, {"ok": True}, None, {}
        if parts == ["api", "docx"] and method == "POST":
            model = self._json()
            filename = re.sub(r"[^A-Za-z0-9 _.-]+", "", model.get("title") or "").strip() or "index"
            return 200, build_docx(model), DOCX_MIME, {
                "Content-Disposition": f'attachment; filename="{filename}.docx"'}
        raise ApiError(404, "Not found")

    def _json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Request too large")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ApiError(400, "Invalid JSON")

    def _send(self, status, body, ctype=None, extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            ctype = ctype or "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def already_running(url):
    try:
        with urllib.request.urlopen(url + "api/indexes", timeout=1) as res:
            return "indexes" in json.loads(res.read())
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Local index builder for SANS/GIAC exams.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    parser.add_argument("--data", help="folder for your indexes (default: ./data next to this script)")
    args = parser.parse_args()

    global DATA_DIR, BACKUP_DIR, TRASH_DIR
    if args.data:
        DATA_DIR = Path(args.data).expanduser().resolve()
        BACKUP_DIR, TRASH_DIR = DATA_DIR / "backups", DATA_DIR / "trash"

    url = f"http://127.0.0.1:{args.port}/"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        if already_running(url):
            print(f"SANS Index Builder is already running at {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return
        raise SystemExit(f"Port {args.port} is in use. Try: python3 sans_index.py --port {args.port + 1}")

    print(f"SANS Index Builder running at {url}")
    print(f"Data folder: {DATA_DIR}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

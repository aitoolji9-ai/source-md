from __future__ import annotations

import io
import json
import secrets
import threading
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from .engine import PRIMARY, PARSER, REPORT, convert, correct_bundle, new_output, validate_bundle
from .model import ConversionError, MAX_INPUT, safe_name, sha256


WEB = Path(__file__).parent / "web"


def preview_html(markdown: str, job: str) -> str:
    # Browser never receives active source HTML. Allow a small reading-only subset.
    if markdown.startswith("---\n"):
        parts = markdown.split("\n---\n", 1)
        markdown = parts[1] if len(parts) == 2 else markdown
    soup = BeautifulSoup(PARSER.render(markdown), "html.parser")
    allowed = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "a", "strong", "em", "del", "ins", "sup", "sub", "code", "pre", "blockquote", "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "br", "hr", "img", "mark"}
    for node in list(soup.find_all(True)):
        if node.name not in allowed:
            node.unwrap()
            continue
        attrs = {}
        for key in ("rowspan", "colspan", "start", "alt", "id", "reversed", "value", "type"):
            if node.has_attr(key):
                attrs[key] = node[key]
        if node.name == "a":
            target = node.get("href", "")
            if target.startswith(("http://", "https://", "mailto:")):
                attrs.update({"href": target, "target": "_blank", "rel": "noopener noreferrer"})
            elif target.startswith("#"):
                attrs["href"] = target
            elif target and not urlparse(target).scheme and ".." not in Path(target).parts:
                attrs["href"] = f"/api/jobs/{job}/files/{target}"
        if node.name == "img":
            target = node.get("src", "")
            if target.startswith("assets/") and ".." not in Path(target).parts and Path(target).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"):
                attrs["src"] = f"/api/jobs/{job}/preview-asset/{target}"
                attrs["loading"] = "lazy"
            else:
                attrs["alt"] = (attrs.get("alt", "") + " [원본 자산은 묶음에서 확인]").strip()
        node.attrs = attrs
    return str(soup)


def create_app(output_root: Path | str = "outputs") -> Flask:
    app = Flask(__name__, static_folder=str(WEB), static_url_path="/static")
    app.config.update(MAX_CONTENT_LENGTH=MAX_INPUT + 1024 * 1024, TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"])
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    app.config["LOCAL_TOKEN"] = token

    @app.before_request
    def local_only():
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not secrets.compare_digest(request.headers.get("X-Local-Token", ""), token):
                abort(403)
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)

    @app.after_request
    def headers(response):
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def home():
        return send_from_directory(WEB, "index.html")

    @app.get("/api/config")
    def config():
        return jsonify(token=token, output_root=str(root), ai_calls=0, max_input_mb=100)

    @app.get("/api/jobs")
    def jobs():
        entries = []
        for folder in sorted(root.iterdir(), reverse=True):
            if folder.is_dir() and (folder / "manifest.json").is_file():
                try:
                    manifest = json.loads((folder / "manifest.json").read_text())
                    entries.append({"id": folder.name, "title": manifest["source"]["filename"], "format": manifest["source"]["format"], "units": len(manifest["units"]), "warnings": len(manifest["unresolved"])})
                except (ValueError, KeyError, OSError):
                    continue
        return jsonify(entries[:100])

    def job_folder(job: str) -> Path:
        folder = (root / job).resolve()
        if folder.parent != root or not folder.is_dir():
            abort(404)
        return folder

    @app.post("/api/convert")
    def conversion():
        options = request.get_json(silent=True) if request.is_json else request.form
        options = options or {}
        kwargs = {"encoding": options.get("encoding", ""), "dynamic": options.get("dynamic") in (True, "true"),
                  "download_assets": options.get("download_assets", "true") not in (False, "false"), "selector": options.get("selector", ""),
                  "ocr": options.get("ocr", "auto")}
        kwargs.update(auto_tabs=options.get('auto_tabs',True) not in (False,'false'),tab_selector=options.get('tab_selector','[role="tab"]'))
        wait=options.get('tab_wait_ms',5000)
        if type(wait) is int:kwargs['tab_wait_ms']=wait
        elif isinstance(wait,str) and wait.isdecimal():kwargs['tab_wait_ms']=int(wait)
        else:return jsonify(error='탭 대기 시간은 정수여야 합니다.'),400
        file = request.files.get("file")
        snapshot=request.files.get('dom_snapshot')
        corrections=request.files.get('ocr_corrections')
        if corrections:kwargs['ocr_corrections']=corrections.read(2*1024*1024+1)
        elif 'ocr_corrections' in options:
            if not isinstance(options['ocr_corrections'],dict):return jsonify(error='교정 입력은 JSON 객체 또는 업로드 파일이어야 합니다.'),400
            kwargs['ocr_corrections']=options['ocr_corrections']
        if snapshot:
            kwargs['dom_snapshot']=snapshot.read(MAX_INPUT+1)
        elif 'dom_snapshot' in options:
            # JSON objects only; never let a request select a local server path.
            if not isinstance(options['dom_snapshot'],dict):
                return jsonify(error='DOM 스냅샷은 JSON 객체 또는 업로드 파일이어야 합니다.'),400
            kwargs['dom_snapshot']=options['dom_snapshot']
        source = options.get("url", "").strip()
        if file:
            name = safe_name(file.filename or "source")
            output = new_output(root, name)
            kwargs.update(data=file.read(MAX_INPUT + 1), filename=name)
            source = name
        elif source:
            output = new_output(root, "webpage")
        else:
            return jsonify(error="파일 또는 URL을 입력해 주세요."), 400
        try:
            result = convert(source, output, **kwargs)
            result["id"] = output.name
            return jsonify(result)
        except (ConversionError, OSError, ValueError) as e:
            return jsonify(error=str(e), id=output.name), 422

    @app.get("/api/jobs/<job>")
    def read_job(job):
        folder = job_folder(job)
        try:
            manifest = json.loads((folder / "manifest.json").read_text())
        except (ValueError, OSError):
            abort(404)
        data = (folder / PRIMARY).read_bytes()
        from .adapters import decode
        markdown, _ = decode(data)
        return jsonify(id=job, manifest=manifest, markdown=markdown, preview=preview_html(markdown, job),
                       structure=json.loads((folder / "structure.json").read_text()),
                       report=(folder / REPORT).read_text(), integrity=validate_bundle(folder), output=str(folder))

    @app.post("/api/jobs/<job>/validate")
    def validation(job):
        return jsonify(validate_bundle(job_folder(job)))

    @app.get('/api/jobs/<job>/review')
    def review_job(job):
        from .review import REVIEW
        folder=job_folder(job);manifest=json.loads((folder/'manifest.json').read_text())
        file=folder/REVIEW
        if REVIEW in manifest['artifacts']:
            if not file.is_file() or sha256(file.read_bytes())!=manifest['artifacts'][REVIEW]:abort(409)
            page=file.read_text()
        else:
            from types import SimpleNamespace
            from .review import write_review
            from .model import Extraction,Unit
            if not validate_bundle(folder)['passed']:abort(409)
            stored=json.loads((folder/'structure.json').read_text())
            extraction=Extraction(stored['title'],stored['format'],metadata=stored['metadata'],warnings=manifest['unresolved'])
            extraction.units=[Unit(**u) for u in stored['units']]
            page=write_review(extraction,SimpleNamespace(root=folder),manifest['source'],[],save=False)
        soup=BeautifulSoup(page,'html.parser')
        data=soup.find('script',id='data');payload=json.loads(data.string)
        for item in payload['items']:
            image=item.get('image','')
            item['image']=f'/api/jobs/{job}/preview-asset/'+image if image.startswith('assets/') and '..' not in Path(image).parts else ''
        data.string=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')
        for script in soup.find_all('script'):
            if script.get('type')=='application/json':continue
            script.clear();script['src']='/static/review.js'
        for style in soup.find_all('style'):
            link=soup.new_tag('link',rel='stylesheet',href='/static/review.css');style.replace_with(link)
        for image in soup.find_all('img',src=True):
            if image['src'].startswith('assets/'):image['src']=f'/api/jobs/{job}/preview-asset/'+image['src']
        for link in soup.find_all('a',href=True):
            link['href']=f'/api/jobs/{job}/files/'+link['href']
        return str(soup)

    @app.post('/api/jobs/<job>/correct')
    def correct_job(job):
        file=request.files.get('ocr_corrections')
        data=file.read(2*1024*1024+1) if file else request.get_json(silent=True)
        if not isinstance(data,(dict,bytes)):return jsonify(error='교정 JSON 파일 또는 객체가 필요합니다.'),400
        output=new_output(root,job+'-corrected')
        try:
            result=correct_bundle(job_folder(job),output,data);result['id']=output.name
            return jsonify(result)
        except (ConversionError,OSError,ValueError) as error:return jsonify(error=str(error),id=output.name),422

    @app.get("/api/jobs/<job>/zip")
    def download_zip(job):
        folder = job_folder(job)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in folder.rglob("*"):
                if path.is_file() and path.resolve().is_relative_to(folder):
                    archive.write(path, path.relative_to(folder))
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"{job}.zip", mimetype="application/zip")

    @app.get("/api/jobs/<job>/files/<path:relative>")
    def download_file(job, relative):
        # Always download source/HTML/SVG/code, never run it in this origin.
        folder = job_folder(job)
        target = (folder / relative).resolve()
        if not target.is_relative_to(folder) or not target.is_file():
            abort(404)
        return send_file(target, as_attachment=True)

    @app.get("/api/jobs/<job>/preview-asset/<path:relative>")
    def preview_asset(job, relative):
        folder = job_folder(job)
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder / "assets") or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"):
            abort(404)
        return send_file(path)

    @app.errorhandler(413)
    def oversized(_):
        return jsonify(error="파일은 한 번에 100MB까지 넣을 수 있습니다."), 413

    return app


def serve(port: int = 8766, open_browser: bool = False, output_root: Path | str = "outputs") -> None:
    app = create_app(output_root)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


"""A controlled local fixture, explicitly allowed only inside this test process."""
import threading
import pytest

from flask import Flask, request
from werkzeug.serving import make_server

from source_preserve_md.model import Assets
from source_preserve_md.network import browser_capture


@pytest.mark.browser
def test_browser_renders_script_details_iframe_and_blocks_post(tmp_path, monkeypatch):
    app = Flask(__name__)
    writes = []

    @app.get("/")
    def page():
        return '''<html><body><h1>Dynamic fixture</h1><p id="late"></p>
        <details><summary>Closed</summary><p>Detail text</p></details>
        <iframe src="/frame"></iframe><script>
        setTimeout(() => document.getElementById('late').textContent = 'Rendered by JavaScript', 80);
        fetch('/write', {method:'POST', body:'must be blocked'}).catch(()=>{});
        </script></body></html>'''

    @app.get("/frame")
    def frame():
        return "<html><body><p>Child frame body</p></body></html>"

    @app.post("/write")
    def write():
        writes.append(request.data)
        return "bad"

    server = make_server("127.0.0.1", 0, app, threaded=True)
    url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Production private-address rejection is independently tested. Only this fixture
    # origin is permitted here; no app/runtime policy is changed.
    def allow_fixture_only(candidate):
        assert candidate == url or candidate.startswith(url + "/")
    monkeypatch.setattr("source_preserve_md.network.public_url", allow_fixture_only)
    try:
        frames, warnings = browser_capture(url, Assets(tmp_path))
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert "Rendered by JavaScript" in frames[0]["html"]
    assert '<details open="">' in frames[0]["html"]
    assert len(frames) == 2 and "Child frame body" in frames[1]["html"]
    assert not writes
    assert any("읽기 이외" in w for w in warnings)
    assert list((tmp_path / "assets").glob("*-webpage.png"))


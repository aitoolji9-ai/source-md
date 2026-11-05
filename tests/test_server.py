import io
import json
import zipfile

import pytest

from source_preserve_md.network import public_url
from source_preserve_md.model import ConversionError
from source_preserve_md.server import create_app, preview_html


def test_upload_read_preview_validate_download(tmp_path):
    app = create_app(tmp_path)
    with app.test_client() as client:
        assert client.get("/").status_code == 200
        token = client.get("/api/config").json["token"]
        headers = {"X-Local-Token": token}
        response = client.post("/api/convert", data={"file": (io.BytesIO(b"# Exact\n\nHello source."), "read.md")}, headers=headers)
        assert response.status_code == 200, response.json
        job = response.json["id"]
        read = client.get(f"/api/jobs/{job}")
        assert read.status_code == 200
        assert "<h1>Exact</h1>" in read.json["preview"]
        assert read.json["markdown"] == "# Exact\n\nHello source."
        assert client.post(f"/api/jobs/{job}/validate", headers=headers).json["passed"]
        source = client.get(f"/api/jobs/{job}/files/source/read.md")
        assert source.data == b"# Exact\n\nHello source."
        assert "attachment" in source.headers["Content-Disposition"]
        downloaded = client.get(f"/api/jobs/{job}/zip")
        with zipfile.ZipFile(io.BytesIO(downloaded.data)) as z:
            assert z.read("source/read.md") == source.data
            assert "manifest.json" in z.namelist()
        assert client.get("/api/jobs").json[0]["id"] == job


def test_local_auth_host_and_script_sanitization(tmp_path):
    app = create_app(tmp_path)
    with app.test_client() as client:
        assert client.post("/api/convert").status_code == 403
        assert client.get("/api/config", headers={"Host": "attacker.example"}).status_code == 400
        assert client.get("/api/config", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    output = preview_html('<script>alert(1)</script><img src="https://tracking.example/x" onerror="alert(1)"><a href="javascript:alert(1)">run</a>', "test")
    assert "<script" not in output and "onerror=" not in output and "javascript:" not in output
    assert 'src="https://' not in output


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/", "http://user:pass@example.com/"])
def test_private_or_authenticated_urls_rejected(url):
    with pytest.raises(ConversionError):
        public_url(url)


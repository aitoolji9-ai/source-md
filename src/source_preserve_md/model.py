from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote


MAX_INPUT = 100 * 1024 * 1024
MAX_EXPANDED = 300 * 1024 * 1024
MAX_UNITS = 1500


class ConversionError(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def safe_name(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(". ")
    return (name or "source")[:180]


def md_url(url: str) -> str:
    return quote(url, safe="/:?#[]@!$&'*+,;=%~._-")


def fence(text: str, language: str = "") -> str:
    longest = max((len(x) for x in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    language = re.sub(r"[^\w+.#-]", "", language)
    return f"{ticks}{language}\n{text}" + ("" if text.endswith("\n") else "\n") + ticks


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


@dataclass
class Unit:
    id: str
    label: str
    source_locator: str
    markdown: str
    text: str = ""
    metadata: dict = field(default_factory=dict)
    code_blocks: list[str] = field(default_factory=list)


@dataclass
class Extraction:
    title: str
    format: str
    units: list[Unit] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)
    # Original MD is a byte-for-byte copy, without generated frontmatter.
    original_markdown: bytes | None = None

    def warn(self, code: str, message: str, locator: str = "document") -> None:
        item = {"code": code, "message": message, "source_locator": locator}
        if item not in self.warnings:
            self.warnings.append(item)

    def unit(self, label: str, locator: str, markdown: str, text: str = "", **metadata) -> Unit:
        if len(self.units) >= MAX_UNITS:
            raise ConversionError(f"단위 수 상한({MAX_UNITS})을 초과했습니다. 입력을 나누어 주세요.")
        unit = Unit(f"unit-{len(self.units)+1:04d}", label, locator, markdown, text, metadata)
        self.units.append(unit)
        return unit

    def structure(self) -> dict:
        return {"title": self.title, "format": self.format, "metadata": self.metadata,
                "units": [asdict(u) for u in self.units]}


class Assets:
    def __init__(self, root: Path):
        self.root = root
        (root / "assets").mkdir(parents=True, exist_ok=True)
        self.records: list[dict] = []
        self.total_bytes = 0

    def add(self, data: bytes, name: str, source: str = "") -> str:
        digest = sha256(data)
        filename = f"{digest[:16]}-{safe_name(name)}"
        relative = "assets/" + filename
        path = self.root / relative
        if path.exists() and sha256(path.read_bytes()) != digest:
            raise ConversionError("기존 자산 해시가 달라 덮어쓰지 않았습니다.")
        if not path.exists():
            if self.total_bytes + len(data) > 500 * 1024 * 1024 or len(self.records) > 25000:
                raise ConversionError("출력 자산의 500MB/25,000개 상한을 초과했습니다. 원본을 나누어 주세요.")
            path.write_bytes(data)
            self.total_bytes += len(data)
        self.records.append({"path": relative, "sha256": digest, "size": len(data), "source": source})
        return relative

    def json(self, name: str, obj, source: str = "") -> str:
        return self.add((json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n").encode(), name, source)


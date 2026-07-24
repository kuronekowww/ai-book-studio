from __future__ import annotations

import hashlib
import html
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FOOTNOTE_RE = re.compile(r"\[(\d+)\]")


@dataclass
class SectionDraft:
    id: str
    parent_id: str | None
    level: int
    position: int
    title: str
    content: str
    kind: str = "section"
    children: list["SectionDraft"] = field(default_factory=list)


@dataclass
class ParsedBook:
    title: str
    sections: list[SectionDraft]
    normalized_text: str
    source_type: str
    diagnostics: dict[str, int | str]


def stable_id(namespace: str, *parts: str) -> str:
    raw = "\x1f".join((namespace, *parts)).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def normalize_text(text: str) -> tuple[str, int]:
    literal_newlines = text.count("\\n")
    if literal_newlines:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip() + "\n", literal_newlines


def parse_markdown(text: str, filename: str) -> ParsedBook:
    normalized, literal_newlines = normalize_text(text)
    lines = normalized.splitlines()
    headings: list[tuple[int, str, int]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line.strip())
        if match:
            headings.append((len(match.group(1)), match.group(2).strip(), index))

    title = Path(filename).stem
    if headings and headings[0][0] == 1 and headings[0][1] not in {"正文", "目录"}:
        title = headings[0][1]

    if not headings:
        headings = [(1, title, 0)]
        lines.insert(0, f"# {title}")

    sections: list[SectionDraft] = []
    stack: list[SectionDraft] = []
    for position, (level, heading, line_index) in enumerate(headings):
        next_line = headings[position + 1][2] if position + 1 < len(headings) else len(lines)
        body = "\n".join(lines[line_index + 1 : next_line]).strip()
        section_id = stable_id("section", filename, str(level), str(position), heading)
        while stack and stack[-1].level >= level:
            stack.pop()
        parent_id = stack[-1].id if stack else None
        section = SectionDraft(
            id=section_id,
            parent_id=parent_id,
            level=level,
            position=position,
            title=heading,
            content=body,
            kind="article" if level >= 4 else "theme",
        )
        if stack:
            stack[-1].children.append(section)
        sections.append(section)
        stack.append(section)

        if level >= 4 and len(body) > 6500:
            chunks = semantic_chunks(body, 2600)
            for chunk_index, chunk in enumerate(chunks, start=1):
                chunk_id = stable_id("segment", section_id, str(chunk_index))
                sections.append(
                    SectionDraft(
                        id=chunk_id,
                        parent_id=section_id,
                        level=min(level + 1, 6),
                        position=position * 1000 + chunk_index,
                        title=f"{heading} · {chunk_index}",
                        content=chunk,
                        kind="semantic_segment",
                    )
                )

    diagnostics = {
        "literal_newlines_restored": literal_newlines,
        "heading_count": len(headings),
        "h3_count": sum(1 for level, _, _ in headings if level == 3),
        "h4_count": sum(1 for level, _, _ in headings if level == 4),
        "footnote_markers": len(FOOTNOTE_RE.findall(normalized)),
        "semantic_segments": sum(1 for section in sections if section.kind == "semantic_segment"),
    }
    return ParsedBook(
        title=title,
        sections=sections,
        normalized_text=normalized,
        source_type="markdown",
        diagnostics=diagnostics,
    )


def semantic_chunks(text: str, target_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        sentences = re.split(r"(?<=[。！？!?])", text)
        paragraphs = [sentence.strip() for sentence in sentences if sentence.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        if current and current_size + len(paragraph) > target_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_size = 0
        current.append(paragraph)
        current_size += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]


class EPUBTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.heading_level: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if re.fullmatch(r"h[1-6]", tag):
            self.heading_level = int(tag[1])
            self.parts.append(f"\n{'#' * self.heading_level} ")
        elif tag in {"p", "div", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n")
            self.heading_level = None
        elif tag in {"p", "div", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        cleaned = re.sub(r"\s+", " ", html.unescape(data))
        if cleaned.strip():
            self.parts.append(cleaned)

    def markdown(self) -> str:
        return "".join(self.parts)


def parse_epub(content: bytes, filename: str) -> ParsedBook:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(
            element
            for element in container.iter()
            if element.tag.endswith("rootfile")
        )
        opf_path = rootfile.attrib["full-path"]
        opf = ElementTree.fromstring(archive.read(opf_path))
        manifest: dict[str, str] = {}
        spine: list[str] = []
        for element in opf.iter():
            if element.tag.endswith("item") and "id" in element.attrib:
                manifest[element.attrib["id"]] = element.attrib.get("href", "")
            if element.tag.endswith("itemref"):
                spine.append(element.attrib.get("idref", ""))

        base = PurePosixPath(opf_path).parent
        markdown_parts: list[str] = []
        for item_id in spine:
            href = manifest.get(item_id)
            if not href:
                continue
            parser = EPUBTextParser()
            parser.feed(decode_text(archive.read(str(base / href))))
            markdown_parts.append(parser.markdown())

    parsed = parse_markdown("\n".join(markdown_parts), filename)
    parsed.source_type = "epub"
    return parsed


def parse_book(content: bytes, filename: str) -> ParsedBook:
    suffix = Path(filename).suffix.lower()
    if suffix == ".epub":
        return parse_epub(content, filename)
    if suffix not in {".md", ".markdown", ".txt"}:
        raise ValueError("仅支持 EPUB、TXT 和 Markdown 文件")
    return parse_markdown(decode_text(content), filename)

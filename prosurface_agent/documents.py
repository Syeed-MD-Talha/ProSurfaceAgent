"""Safe, bounded text extraction for supported chat attachments."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_CHARS = 50_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 30 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2_000


def extract_document(raw: bytes, filename: str) -> str:
    """Extract text from DOCX, XLSX, TXT, or CSV without executing content."""
    if not raw:
        raise ValueError("the file is empty")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("the file is too large (maximum 10 MB)")

    suffix = Path(filename).suffix.lower()
    if suffix in (".txt", ".csv"):
        text = _decode_text(raw)
        if suffix == ".csv":
            text = _format_csv(text)
        return _limit_text(text)
    if suffix == ".docx":
        return _extract_docx(raw)
    if suffix == ".xlsx":
        return _extract_xlsx(raw)
    raise ValueError("supported document formats are DOCX, XLSX, TXT, and CSV")


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("the text file encoding is not supported")

    if "\x00" in text:
        raise ValueError("the file does not look like a readable text document")
    return text


def _format_csv(text: str) -> str:
    output = []
    try:
        for row_index, row in enumerate(csv.reader(io.StringIO(text))):
            if row_index >= 1_000:
                output.append("[remaining CSV rows omitted]")
                break
            output.append(" | ".join(cell.strip() for cell in row[:100]))
    except csv.Error as exc:
        raise ValueError(f"the CSV file is malformed: {exc}") from exc
    return "\n".join(output)


def _limit_text(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("no readable text was found")
    if len(text) > MAX_EXTRACTED_CHARS:
        return text[:MAX_EXTRACTED_CHARS] + "\n[remaining document text omitted]"
    return text


def _checked_archive(raw: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        members = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("the file is not a valid Office document") from exc
    if len(members) > MAX_ARCHIVE_MEMBERS:
        archive.close()
        raise ValueError("the Office document contains too many parts")
    if sum(item.file_size for item in members) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        archive.close()
        raise ValueError("the Office document expands beyond the safe size limit")
    return archive


def _read_xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        info = archive.getinfo(name)
        if info.file_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("an Office document section is too large")
        return ElementTree.fromstring(archive.read(name))
    except KeyError as exc:
        raise ValueError("the Office document is missing required content") from exc
    except ElementTree.ParseError as exc:
        raise ValueError("the Office document contains invalid XML") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_docx(raw: bytes) -> str:
    archive = _checked_archive(raw)
    try:
        root = _read_xml(archive, "word/document.xml")
        paragraphs = []
        for paragraph in (node for node in root.iter() if _local_name(node.tag) == "p"):
            pieces = [node.text or "" for node in paragraph.iter() if _local_name(node.tag) == "t"]
            line = "".join(pieces).strip()
            if line:
                paragraphs.append(line)
        return _limit_text("\n".join(paragraphs))
    finally:
        archive.close()


def _extract_xlsx(raw: bytes) -> str:
    archive = _checked_archive(raw)
    try:
        names = {item.filename for item in archive.infolist()}
        sheet_names = sorted(
            (name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")),
            key=_natural_sheet_key,
        )
        if not sheet_names:
            raise ValueError("the spreadsheet has no readable worksheets")

        shared_strings = []
        if "xl/sharedStrings.xml" in names:
            shared_root = _read_xml(archive, "xl/sharedStrings.xml")
            for item in (node for node in shared_root if _local_name(node.tag) == "si"):
                shared_strings.append(
                    "".join(node.text or "" for node in item.iter() if _local_name(node.tag) == "t")
                )

        lines = []
        char_count = 0
        for sheet_index, sheet_name in enumerate(sheet_names[:50], start=1):
            root = _read_xml(archive, sheet_name)
            rows = [node for node in root.iter() if _local_name(node.tag) == "row"]
            if not rows:
                continue
            heading = f"Sheet {sheet_index}:"
            lines.append(heading)
            char_count += len(heading)
            for row in rows:
                values = []
                for cell in (node for node in row if _local_name(node.tag) == "c"):
                    value = _cell_value(cell, shared_strings)
                    if value:
                        values.append(value)
                if values:
                    line = " | ".join(values[:100])
                    lines.append(line)
                    char_count += len(line)
                if char_count > MAX_EXTRACTED_CHARS:
                    return _limit_text("\n".join(lines))
        return _limit_text("\n".join(lines))
    finally:
        archive.close()


def _natural_sheet_key(name: str) -> tuple[int, str]:
    number = "".join(character for character in Path(name).stem if character.isdigit())
    return (int(number or "0"), name)


def _cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    value = next((node.text or "" for node in cell if _local_name(node.tag) == "v"), "")
    if cell_type == "s" and value:
        try:
            return shared_strings[int(value)]
        except (ValueError, IndexError):
            return ""
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter() if _local_name(node.tag) == "t")
    return value

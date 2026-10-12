from io import BytesIO
from zipfile import ZipFile

import pytest

from app.exports import export_transcript


@pytest.fixture
def meeting():
    return {
        "id": "test-meeting",
        "title": "Жиналыс / Встреча / Meeting",
        "source": "upload",
        "language": "kk",
        "status": "completed",
        "created_at": "2026-10-12T08:00:00+00:00",
        "updated_at": "2026-10-12T08:30:00Z",
        "segments": [
            {
                "id": "one",
                "start": 0.25,
                "end": 61.2,
                "speaker": "Әлия",
                "text": "Ә Ғ Қ Ң Ө Ұ Ү Һ І — Қазақша мәтін. Привет, мир! Hello, world!",
            },
            {
                "id": "two",
                "start": 3661,
                "end": 3663,
                "speaker": None,
                "text": "<b>not markup</b> & [not a link](https://example.org)\nЖаңа жол",
            },
        ],
    }


@pytest.mark.parametrize("format", ["txt", "md"])
def test_text_exports_preserve_unicode(meeting, format):
    content, mime, filename = export_transcript(meeting, format)
    decoded = content.decode("utf-8")
    assert "Ә Ғ Қ Ң Ө Ұ Ү Һ І" in decoded
    assert "Привет, мир" in decoded
    assert "Hello, world" in decoded
    assert "01:01:01" in decoded
    assert "2026-10-12 08:00:00+00:00" in decoded
    assert "не подтверждают личность" in decoded
    assert filename.endswith("." + format)
    assert "/" not in filename and "\n" not in filename
    assert mime.startswith("text/")


def test_markdown_escapes_active_content(meeting):
    content, _, _ = export_transcript(meeting, "md")
    text = content.decode()
    assert "<b>" not in text
    assert r"\[not a link\]" in text
    assert "&lt;b&gt;" in text


def test_docx_is_valid_and_contains_unicode(meeting):
    content, mime, _ = export_transcript(meeting, "docx")
    assert content[:2] == b"PK"
    assert mime.endswith("wordprocessingml.document")
    with ZipFile(BytesIO(content)) as archive:
        xml = archive.read("word/document.xml").decode()
    assert "Ә Ғ Қ Ң Ө Ұ Ү Һ І" in xml
    assert "Привет, мир! Hello, world!" in xml
    assert "&lt;b&gt;not markup&lt;/b&gt;" in xml
    assert "01:01:01" in xml


def test_pdf_is_valid_and_contains_unicode(meeting):
    pypdf = pytest.importorskip("pypdf")
    content, mime, _ = export_transcript(meeting, "pdf")
    assert content.startswith(b"%PDF-")
    assert mime == "application/pdf"
    reader = pypdf.PdfReader(BytesIO(content))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "Ә Ғ Қ Ң Ө Ұ Ү Һ І" in text
    assert "Привет, мир! Hello, world!" in text
    assert "<b>not markup</b>" in text
    assert "01:01:01" in text


@pytest.mark.parametrize("format", ["txt", "md", "docx", "pdf"])
def test_empty_transcript_exports_without_fabrication(format):
    content, _, name = export_transcript({"title": "Пустая встреча", "segments": []}, format)
    assert content
    assert name == "transcript." + format
    if format in {"txt", "md"}:
        assert "пока отсутствует" in content.decode()


def test_unsupported_format_rejected(meeting):
    with pytest.raises(ValueError, match="Unsupported"):
        export_transcript(meeting, "html")


def test_malformed_metadata_and_control_characters(meeting):
    meeting["title"] = "../unsafe\r\n\x00name"
    meeting["segments"][0]["start"] = "NaN"
    meeting["segments"][0]["text"] += "\x00\x01"
    for format in ["txt", "md", "docx", "pdf"]:
        content, _, name = export_transcript(meeting, format)
        assert content
        assert name == "unsafe-name." + format


@pytest.mark.parametrize("format", ["txt", "md"])
def test_provisional_segments_are_labelled(meeting, format):
    meeting["segments"][0]["final"] = False
    content, _, _ = export_transcript(meeting, format)
    assert "предварительный фрагмент" in content.decode("utf-8")

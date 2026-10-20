"""Resume reader tool for PDF, Word (.docx), and plain-text resumes.

Referenced from the crew JSON as `custom:resume_reader_tool`. The JSON loader
imports this module and instantiates the first BaseTool subclass it finds, so
this file intentionally defines exactly one. `read_resume` is shared with the
blind screening guardrails, so both see exactly the same text.
"""

from pathlib import Path

from crewai.tools import BaseTool
from docx import Document
from docx.table import Table
from pypdf import PdfReader

RESUMES_DIR = Path(__file__).resolve().parent.parent / "resumes"
RESUME_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}

# A text layer shorter than this means the PDF is a scanned image.
MIN_TEXT_CHARS = 40


def resume_files() -> list[Path]:
    """Every resume in the folder, skipping Office lock files and hidden files."""
    return sorted(
        path
        for path in RESUMES_DIR.iterdir()
        if path.suffix.lower() in RESUME_EXTENSIONS
        and not path.name.startswith(("~$", "."))
    )


def read_resume(path: Path) -> str:
    """Return a resume's text, or '' when the file has no readable text."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix == ".docx":
        text = _docx_text(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = text.strip()
    return text if len(text) >= MIN_TEXT_CHARS else ""


def _docx_text(path: Path) -> str:
    doc = Document(path)
    lines = []
    # Word templates often put the name and contact details in the page header.
    for section in doc.sections:
        for part in (section.header, section.footer):
            if not part.is_linked_to_previous:
                lines += [p.text for p in part.paragraphs]
    # Paragraphs and tables in document order; many templates lay out
    # sections as tables.
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    text = cell.text.strip()
                    if text and (not cells or cells[-1] != text):  # merged cells repeat
                        cells.append(text)
                lines.append(" | ".join(cells))
        else:
            lines.append(block.text)
    return "\n".join(line for line in lines if line.strip())


class ResumeReaderTool(BaseTool):
    name: str = "Resume Reader Tool"
    description: str = (
        "Reads every resume in the project's resumes folder (PDF, Word, or "
        "plain text) and returns each file name followed by the resume's full "
        "text. Takes no input. Call it once to get all resumes."
    )

    def _run(self) -> str:
        files = resume_files()
        if not files:
            return f"No resumes found in {RESUMES_DIR}."

        sections = []
        for path in files:
            try:
                text = read_resume(path) or (
                    "[no readable text: this looks like a scanned image. "
                    "Skip this file.]"
                )
            except Exception as exc:
                text = f"[could not read this file: {exc}. Skip this file.]"
            sections.append(f"=== RESUME FILE: {path.name} ===\n{text}")
        return "\n\n".join(sections)

"""Resume reader tool backed by pypdf.

Referenced from the crew JSON as `custom:resume_reader_tool`. The JSON loader
imports this module and instantiates the first BaseTool subclass it finds, so
this file intentionally defines exactly one.
"""

from pathlib import Path

from crewai.tools import BaseTool
from pypdf import PdfReader

RESUMES_DIR = Path(__file__).resolve().parent.parent / "resumes"


class ResumeReaderTool(BaseTool):
    name: str = "Resume Reader Tool"
    description: str = (
        "Reads every PDF resume in the project's resumes folder and returns "
        "each file name followed by the resume's full text. Takes no input. "
        "Call it once to get all resumes."
    )

    def _run(self) -> str:
        pdfs = sorted(RESUMES_DIR.glob("*.pdf"))
        if not pdfs:
            return f"No PDF resumes found in {RESUMES_DIR}."

        sections = []
        for pdf in pdfs:
            try:
                text = "\n".join(
                    page.extract_text() or "" for page in PdfReader(pdf).pages
                ).strip()
            except Exception as exc:
                text = f"[could not read this file: {exc}]"
            sections.append(
                f"=== RESUME FILE: {pdf.name} ===\n"
                f"{text or '[no extractable text]'}"
            )
        return "\n\n".join(sections)

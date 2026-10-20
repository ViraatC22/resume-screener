# /// script
# requires-python = ">=3.10"
# dependencies = ["fpdf2>=2.8", "python-docx>=1.1"]
# ///
"""Generate the six sample resumes in resumes/.

Two strong, two middling, and two weak candidates for a Python backend role.
File formats (PDF, Word, plain text), layouts, section names, date formats, and
fonts deliberately differ so the crew has to cope with real-world variety. File
names never reveal the tier.

Run from the project root:

    uv run scripts/generate_resumes.py
"""

from pathlib import Path

from docx import Document
from docx.shared import Pt
from fpdf import FPDF

OUT_DIR = Path(__file__).resolve().parent.parent / "resumes"


def new_pdf(font: str) -> FPDF:
    pdf = FPDF(format="Letter")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_font(font, size=10)
    return pdf


def heading(pdf: FPDF, font: str, text: str, rule: bool = True) -> None:
    pdf.ln(3)
    pdf.set_font(font, "B", 11)
    pdf.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
    if rule:
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(1.5)
    pdf.set_font(font, size=10)


def job(pdf: FPDF, font: str, title: str, dates: str, company: str, bullets: list[str], bullet: str = "-") -> None:
    pdf.set_font(font, "B", 10)
    pdf.cell(0, 5, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, "I", 10)
    pdf.cell(0, 5, f"{company}  |  {dates}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font, size=10)
    for item in bullets:
        pdf.multi_cell(0, 5, f"{bullet} {item}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def para(pdf: FPDF, text: str) -> None:
    pdf.multi_cell(0, 5, text, new_x="LMARGIN", new_y="NEXT")


# --- Strong #1: classic single column, reverse-chronological -----------------
def priya_raman() -> FPDF:
    f = "Helvetica"
    pdf = new_pdf(f)
    pdf.set_font(f, "B", 20)
    pdf.cell(0, 9, "Priya Raman", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(f, size=10)
    pdf.cell(0, 5, "Senior Backend Engineer  |  Austin, TX", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "priya.raman@example.com  |  (512) 555-0143  |  github.com/priyaraman", align="C", new_x="LMARGIN", new_y="NEXT")

    heading(pdf, f, "SUMMARY")
    para(pdf, "Backend engineer with 6 years of experience designing and scaling Python services. "
              "Specialize in Django and FastAPI APIs, PostgreSQL performance tuning, and AWS infrastructure. "
              "Comfortable owning services end to end, from design docs to on-call.")

    heading(pdf, f, "EXPERIENCE")
    job(pdf, f, "Senior Backend Engineer", "Mar 2023 - Present", "Lumen Health (Series C, 400 employees)", [
        "Led the redesign of the claims-processing API in FastAPI, cutting p95 latency from 820ms to 140ms.",
        "Migrated 3 Django monoliths to containerized services on AWS ECS; reduced hosting cost by 31%.",
        "Introduced pytest + contract testing in CI, raising coverage from 48% to 87%.",
        "Mentor 4 engineers; run the backend guild's design review.",
    ])
    job(pdf, f, "Backend Engineer", "Jun 2020 - Feb 2023", "Parcelly (logistics SaaS)", [
        "Built the shipment-tracking service (Django REST Framework, Celery, Redis) handling 2M events/day.",
        "Tuned PostgreSQL indexes and queries, cutting the nightly reporting job from 4h to 25min.",
        "Designed an idempotent webhook system used by 1,200+ customer integrations.",
    ])
    job(pdf, f, "Software Engineering Intern", "May 2019 - Aug 2019", "Bluebonnet Bank", [
        "Wrote Python ETL scripts that automated a manual reconciliation process.",
    ])

    heading(pdf, f, "SKILLS")
    para(pdf, "Languages: Python, SQL, Go (working knowledge), Bash")
    para(pdf, "Frameworks: FastAPI, Django, DRF, Celery, SQLAlchemy, pytest")
    para(pdf, "Data & Infra: PostgreSQL, Redis, Kafka, Docker, AWS (ECS, RDS, SQS, Lambda), Terraform, GitHub Actions")

    heading(pdf, f, "EDUCATION")
    para(pdf, "B.S. Computer Science, University of Texas at Austin, 2020")
    return pdf


# --- Strong #2: two-column with a shaded sidebar -----------------------------
def daniel_okafor() -> FPDF:
    f = "Times"
    pdf = new_pdf(f)
    sidebar_w = 62
    pdf.set_fill_color(232, 236, 241)
    pdf.rect(0, 0, sidebar_w, pdf.h, style="F")

    # Sidebar
    pdf.set_left_margin(8)
    pdf.set_right_margin(pdf.w - sidebar_w + 6)
    pdf.set_xy(8, 16)
    pdf.set_font(f, "B", 18)
    pdf.multi_cell(0, 8, "Daniel\nOkafor", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(f, size=9.5)
    pdf.ln(2)
    para(pdf, "d.okafor@example.com\n+1 646-555-0178\nBrooklyn, NY\nlinkedin.com/in/dokafor")
    for title, lines in [
        ("Core Skills", "Python 3\nGo\nAsyncio / aiohttp\nFastAPI, Flask\nPostgreSQL, MongoDB\nKafka, RabbitMQ\nKubernetes, Helm\nGCP, AWS\nPrometheus, Grafana\nCI/CD"),
        ("Education", "M.S. Computer Science\nColumbia University\n2018\n\nB.Eng. Software Eng.\nUniversity of Lagos\n2016"),
        ("Certifications", "Certified Kubernetes\nApplication Developer\n(CKAD), 2022"),
    ]:
        pdf.ln(3)
        pdf.set_font(f, "B", 11)
        pdf.cell(0, 6, title.upper(), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(f, size=9.5)
        para(pdf, lines)

    # Main column
    pdf.set_left_margin(sidebar_w + 8)
    pdf.set_right_margin(16)
    pdf.set_xy(sidebar_w + 8, 16)
    heading(pdf, f, "Profile")
    para(pdf, "Backend engineer (5+ yrs) building high-throughput distributed systems in Python and Go. "
              "I care about reliability, observability, and clean APIs.")
    heading(pdf, f, "Experience")
    job(pdf, f, "Backend Engineer II", "2021 - present", "Streamline Payments, New York", [
        "Own the payment-routing service (Python/asyncio) processing ~$40M/day at 99.99% uptime.",
        "Designed a Kafka-based event pipeline replacing nightly batch jobs; settlement now near real-time.",
        "Cut cloud spend 22% by right-sizing Kubernetes workloads and adding autoscaling.",
        "Wrote the team's incident-response runbook; led 15+ postmortems.",
    ], bullet="*")
    job(pdf, f, "Software Engineer", "2018 - 2021", "Gridline Analytics, New York", [
        "Built Flask and FastAPI services for an energy-forecasting product used by 30 utilities.",
        "Rewrote a hot-path ingestion worker in Go, increasing throughput 6x.",
        "Introduced structured logging and Prometheus metrics across 12 services.",
    ], bullet="*")
    heading(pdf, f, "Selected Projects")
    para(pdf, "* pg-migrate-lite - open-source zero-downtime PostgreSQL migration helper (900+ GitHub stars).")
    para(pdf, "* Speaker, PyGotham 2024: \"Backpressure in asyncio services\".")
    return pdf


# --- Middling #1: Word template, contact details in the page header, skills table
def emily_carter() -> Document:
    doc = Document()
    header = doc.sections[0].header.paragraphs[0]
    header.text = "Columbus, OH - 614.555.0190 - emily.carter@example.com"

    title = doc.add_paragraph()
    run = title.add_run("EMILY CARTER")
    run.bold = True
    run.font.size = Pt(18)

    doc.add_heading("Objective", level=2)
    doc.add_paragraph("Motivated junior developer seeking a backend software engineering position where I can "
                      "grow my Python skills and contribute to a collaborative team.")

    doc.add_heading("Work History", level=2)
    for title_text, meta, bullets in [
        ("Junior Software Developer", "Midwest Insurance Group  |  06/2024 - present", [
            "Write Python scripts to automate internal reports.",
            "Helped maintain a Flask app used by the claims team.",
            "Worked on SQL queries for the data team.",
            "Participated in code reviews and daily standups.",
        ]),
        ("IT Support Intern", "Ohio State University IT Services  |  01/2023 - 05/2024", [
            "Resolved help-desk tickets and set up laptops.",
            "Wrote a small Python script to clean up user account spreadsheets.",
        ]),
    ]:
        doc.add_paragraph().add_run(title_text).bold = True
        doc.add_paragraph().add_run(meta).italic = True
        for item in bullets:
            doc.add_paragraph(item, style="List Bullet")

    doc.add_heading("Technical Skills", level=2)
    table = doc.add_table(rows=0, cols=2)
    for label, value in [
        ("Languages", "Python, SQL (MySQL), HTML/CSS, some JavaScript"),
        ("Frameworks", "Flask"),
        ("Tools", "Git, Excel, familiar with Docker"),
    ]:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = label, value

    doc.add_heading("Education", level=2)
    doc.add_paragraph("B.S. Information Technology - The Ohio State University - May 2024 - GPA 3.2")

    doc.add_heading("Projects", level=2)
    doc.add_paragraph("Personal budget tracker web app (Flask + SQLite)", style="List Bullet")
    doc.add_paragraph("Followed a tutorial to build a REST API for a to-do list", style="List Bullet")
    return doc


# --- Middling #2: skills-first functional resume, career switcher ------------
def marcus_lee() -> FPDF:
    f = "Helvetica"
    pdf = new_pdf(f)
    pdf.set_font(f, "B", 17)
    pdf.cell(0, 8, "Marcus Lee", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(f, "I", 10)
    pdf.cell(0, 5, "Full-Stack Developer (Backend Focus)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(f, size=10)
    pdf.cell(0, 5, "marcus.lee.dev@example.com / 773-555-0112 / Chicago, IL / Open to remote", new_x="LMARGIN", new_y="NEXT")

    heading(pdf, f, "KEY SKILLS")
    para(pdf, "Backend: Node.js, Express, REST APIs, some Python (Django tutorials, scripting)")
    para(pdf, "Databases: MongoDB, PostgreSQL (basic)")
    para(pdf, "Frontend: React, TypeScript")
    para(pdf, "Other: Git, Jest, Heroku, Agile/Scrum")

    heading(pdf, f, "PROFESSIONAL HISTORY")
    job(pdf, f, "Full-Stack Developer", "Feb 2025 - Present (1 yr 7 mos)", "BrightCart (e-commerce startup)", [
        "Built and maintain Express endpoints for the product catalog and checkout flow.",
        "Added Jest tests to the order service; fixed bugs reported by the support team.",
        "Collaborated with designers to ship a React admin dashboard.",
    ], bullet=">")
    job(pdf, f, "Operations Coordinator", "Aug 2019 - Dec 2024", "Lakeshore Logistics", [
        "Managed delivery schedules for a team of 25 drivers.",
        "Taught myself Python to automate a weekly Excel report, saving ~3 hours/week.",
    ], bullet=">")

    heading(pdf, f, "TRAINING")
    para(pdf, "Full-Stack Web Development Bootcamp - Code Chicago, 2024 (16 weeks)")
    para(pdf, "B.A. Business Administration - Northeastern Illinois University, 2019")
    return pdf


# --- Weak #1: plain-text resume, unrelated experience, typos, very short ------
def tyler_brooks() -> str:
    return """Tyler Brooks
partyking99@example.com   cell: 555 0167

ABOUT ME
Hard worker and people person looking to get into tech becuase it pays good.
I am a fast lerner and good with computers.

EXPERIANCE
Shift Manager, QuickMart Convenience Stores (2021-2025)
  * Opened and closed store, counted registers
  * Made employee schedules
  * Delt with customer complaints
Cashier, Burger Barn (2019-2021)
  * Took orders and handled cash

SKILLS
Microsoft Word, Excel, social media, typing 45 wpm, team player

EDUCATION
Lincoln High School diploma 2019

References available upon request
"""


# --- Weak #2: no sections, keyword stuffing, contact info buried at the end --
def kevin_walsh() -> FPDF:
    f = "Courier"
    pdf = new_pdf(f)
    pdf.set_font(f, "B", 12)
    pdf.cell(0, 6, "KEVIN WALSH - ROCKSTAR NINJA DEVELOPER", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(f, size=9)
    pdf.ln(2)
    para(pdf, "I AM A PASSIONATE SELF-STARTER AND EXPERT IN PYTHON JAVA C++ C# RUST GO RUBY PHP SWIFT KOTLIN SCALA "
              "HASKELL PERL AI ML DEEP LEARNING BLOCKCHAIN WEB3 CLOUD AWS AZURE GCP KUBERNETES DOCKER MICROSERVICES "
              "BIG DATA HADOOP SPARK REACT ANGULAR VUE AND MORE. I HAVE WORKED AT MANY COMPANIES DOING MANY THINGS "
              "INCLUDING WEBSITES AND APPS AND DATABASES. I BUILT AN AI THAT CAN DO ANYTHING. I AM THE BEST CODER "
              "MY FRIENDS KNOW. I DID FREELANCE FOR A FEW MONTHS AND THEN WORKED AT A STARTUP THAT CLOSED AND THEN "
              "ANOTHER STARTUP FOR 6 WEEKS AND THEN I DID SOME CRYPTO TRADING. I LEARN EVERYTHING ON YOUTUBE SO I "
              "DONT NEED A DEGREE. LOOKING FOR SENIOR OR LEAD OR CTO ROLES ONLY. SALARY EXPECTATIONS 300K+.")
    pdf.ln(3)
    para(pdf, "SKILLS SKILLS SKILLS: PYTHON PYTHON PYTHON BACKEND BACKEND API API SQL NOSQL REST GRAPHQL SOAP "
              "AGILE SCRUM KANBAN LEADERSHIP SYNERGY")
    pdf.ln(3)
    para(pdf, "contact me: kevinwalsh@example.com  555-0199")
    return pdf


RESUMES = {
    "Priya_Raman_Resume.pdf": priya_raman,
    "daniel-okafor-cv.pdf": daniel_okafor,
    "EmilyCarter_resume.docx": emily_carter,
    "marcus_lee_2026.pdf": marcus_lee,
    "TylerBrooks.txt": tyler_brooks,
    "KW_resume_final_v2.pdf": kevin_walsh,
}


if __name__ == "__main__":
    OUT_DIR.mkdir(exist_ok=True)
    for filename, build in RESUMES.items():
        resume = build()
        path = OUT_DIR / filename
        if isinstance(resume, str):
            path.write_text(resume)
        elif isinstance(resume, FPDF):
            resume.output(str(path))
        else:
            resume.save(str(path))
        print(f"wrote resumes/{filename}")

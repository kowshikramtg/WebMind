"""Generate the demo's PDF documents and structured database.

Usage:
    python examples/basic-website/setup_demo.py            # writes examples/basic-website/generated/
    python examples/basic-website/setup_demo.py --out DIR

Everything here is fictional and deterministic (fixed random seed). Requires the
``reports`` extra for PDF generation: pip install "webmind[reports]".
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEPARTMENTS = ["engineering", "research", "admissions", "finance"]


def build_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(42)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at DATE NOT NULL,
            budget REAL NOT NULL
        );
        CREATE TABLE monthly_metrics (
            month DATE NOT NULL,
            department TEXT NOT NULL,
            registrations INTEGER NOT NULL,
            revenue REAL NOT NULL
        );
        """
    )
    adjectives = ["Adaptive", "Bright", "Clear", "Distributed", "Efficient", "Fast", "Green", "Open"]
    nouns = ["Grid", "Portal", "Sensor", "Pipeline", "Archive", "Network", "Lab", "Platform"]
    projects = []
    start = date(2026, 1, 1)
    for i in range(1, 121):
        created = start + timedelta(days=rng.randrange(0, 334))
        projects.append(
            (
                i,
                f"{rng.choice(adjectives)} {rng.choice(nouns)} {i}",
                rng.choice(DEPARTMENTS),
                rng.choice(["planned", "active", "active", "completed"]),
                created.isoformat(),
                round(rng.uniform(5_000, 120_000), 2),
            )
        )
    con.executemany("INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)", projects)
    metrics = []
    for m in range(1, 12):
        for d in DEPARTMENTS:
            base = {"engineering": 120, "research": 60, "admissions": 200, "finance": 30}[d]
            regs = int(base * (1 + 0.04 * m) + rng.randint(-10, 10))
            revenue = round(regs * rng.uniform(38, 46), 2)
            metrics.append((date(2026, m, 1).isoformat(), d, regs, revenue))
    con.executemany("INSERT INTO monthly_metrics VALUES (?, ?, ?, ?)", metrics)
    con.commit()
    con.close()


def build_pdfs(out: Path) -> None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        print("reportlab not installed — skipping PDF generation (pip install 'webmind[reports]')")
        return
    out.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()

    def make(name: str, title: str, pages: list[list[tuple[str, str]]]) -> None:
        doc = SimpleDocTemplate(str(out / name), pagesize=A4, title=title, invariant=1)
        story = []
        for p_i, page in enumerate(pages):
            for kind, text in page:
                story.append(Paragraph(text, styles["Heading2" if kind == "h" else "BodyText"]))
                story.append(Spacer(1, 8))
            if p_i < len(pages) - 1:
                story.append(PageBreak())
        doc.build(story)

    make(
        "student-handbook-2026.pdf",
        "Student Handbook 2026",
        [
            [
                ("h", "Student Handbook 2026"),
                ("p", "This handbook describes rules and services for students of Lumen Valley Institute."),
            ],
            [
                ("h", "Examinations"),
                (
                    "p",
                    "The examination timetable is published on the student portal four weeks before each exam period. Exam periods take place in January and June.",
                ),
            ],
            [
                ("h", "Library"),
                (
                    "p",
                    "The library is open from 8:00 to 22:00 on weekdays. Students may borrow up to 10 books for four weeks.",
                ),
            ],
        ],
    )
    make(
        "research-report-q1-2026.pdf",
        "Research Report Q1 2026",
        [
            [
                ("h", "Research Report Q1 2026"),
                (
                    "p",
                    "In the first quarter the research department completed 7 consulting engagements and filed 2 patent applications.",
                ),
            ],
            [
                ("h", "Highlights"),
                ("p", "The low-power sensor grid prototype reduced energy consumption by 31 percent in field tests."),
            ],
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "generated")
    args = parser.parse_args()
    build_database(args.out / "records.db")
    build_pdfs(args.out / "docs")
    print(f"demo assets written to {args.out.resolve()}")


if __name__ == "__main__":
    main()

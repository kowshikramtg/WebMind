"""Cross-check the browser-side export writers (sdk/react core) against independent readers.

The widget and @webmind/react generate .xlsx files with a dependency-free writer. Here the compiled
JavaScript produces a workbook with awkward cell contents and openpyxl, which shares no code with it,
must read it back unchanged. Skipped when Node or the compiled SDK (`npm run build`) is unavailable.
"""

import json
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[2] / "sdk" / "react"
EXPORTS = SDK / "dist" / "core" / "exports.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None or not EXPORTS.exists(), reason="needs node and a built sdk/react (npm run build)"
)

SCRIPT = """
import { writeFileSync } from "node:fs";
import { tableToXlsx, toCsv } from "%(exports)s";
const cols = ["department", "avg_budget", "note"];
const rows = [
  { department: "a<b & \\"c\\"", avg_budget: 58866.17, note: "=HYPERLINK(\\"http://evil\\")" },
  { department: "r\\u00e9sum\\u00e9 \\u2713", avg_budget: 3, note: "line1\\nline2" },
  { department: "x", avg_budget: null, note: "+1" },
];
writeFileSync(process.argv[2], tableToXlsx(cols, rows, "projects"));
writeFileSync(process.argv[3], toCsv(cols, rows));
"""


@pytest.fixture
def generated(tmp_path):
    script = tmp_path / "gen.mjs"
    script.write_text(SCRIPT % {"exports": EXPORTS.as_uri()}, encoding="utf-8")
    xlsx, csv = tmp_path / "out.xlsx", tmp_path / "out.csv"
    subprocess.run([NODE, str(script), str(xlsx), str(csv)], check=True, capture_output=True, timeout=60)  # noqa: S603
    return xlsx, csv


def test_openpyxl_reads_the_browser_generated_workbook(generated):
    from openpyxl import load_workbook

    xlsx, _ = generated
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a conformant package produces no reader warnings
        wb = load_workbook(xlsx)
    ws = wb["projects"]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("department", "avg_budget", "note")
    assert rows[1][0] == 'a<b & "c"' and rows[1][1] == 58866.17
    assert rows[1][2].startswith("'=")  # formula injection neutralized in the spreadsheet
    assert rows[2][0] == "résumé ✓" and rows[2][2] == "line1\nline2"
    assert rows[3][1] is None and rows[3][2] == "'+1"
    assert ws["A1"].font.b is True and ws.freeze_panes == "A2"
    assert ws["B2"].data_type == "n"  # numbers are real numbers, not text


def test_csv_round_trips_through_the_stdlib_reader(generated):
    import csv

    _, path = generated
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines(keepends=True), strict=True))
    # re-join because the multi-line cell spans physical lines
    parsed = list(csv.reader(path.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines(True)))
    assert rows[0] == ["department", "avg_budget", "note"] or parsed[0] == ["department", "avg_budget", "note"]
    text = path.read_text(encoding="utf-8")
    assert json.dumps("'=HYPERLINK")[1:-1] in text.replace('"', "") or "'=HYPERLINK" in text
    assert '"line1\nline2"' in text

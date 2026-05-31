"""
Convierte Capitulo6_Desarrollo_v3.md a .docx con estilos de tesis universitaria.

Maneja:
  - Encabezados ATX (#, ##, ###)
  - Tablas Markdown (| col | col |)
  - Bloques de código (``` ... ```)
  - Bloques de cita (>)
  - Listas con guion (-)
  - Negrita **...** y cursiva *...* o _..._
  - Enlaces [texto](url) -> se conserva el texto
  - Líneas horizontales ---
"""

import re
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


ROOT = Path(__file__).resolve().parent.parent
MD_PATH = ROOT / "Capitulo6_Desarrollo_v3.md"
DOCX_PATH = ROOT / "Capitulo6_Desarrollo_v3.docx"


# ---------------------------------------------------------------------------
# Estilos base
# ---------------------------------------------------------------------------

def setup_styles(doc: Document) -> None:
    """Aplica estilos académicos: Times New Roman 12, justificado, 1.5 interlineado."""
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for level, size in [(1, 16), (2, 14), (3, 13)]:
        style = doc.styles[f"Heading {level}"]
        style.font.name = "Times New Roman"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0x1F, 0x2A, 0x44)


def set_cell_shading(cell, color_hex: str) -> None:
    """Aplica color de fondo a una celda."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)
    tc_pr.append(shd)


# ---------------------------------------------------------------------------
# Render de runs con inline (negrita, cursiva, código)
# ---------------------------------------------------------------------------

INLINE_PATTERN = re.compile(
    r"(\*\*[^*]+\*\*)"      # **negrita**
    r"|(\*[^*\n]+\*)"        # *cursiva*
    r"|(`[^`]+`)"            # `código`
    r"|(\[[^\]]+\]\([^\)]+\))"  # [texto](url)
)


def add_runs_with_inline(paragraph, text: str) -> None:
    """Añade runs aplicando inline formatting básico."""
    text = text.replace("­", "")
    pos = 0
    for match in INLINE_PATTERN.finditer(text):
        if match.start() > pos:
            paragraph.add_run(text[pos:match.start()])

        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            run.bold = True
        elif token.startswith("*"):
            run = paragraph.add_run(token[1:-1])
            run.italic = True
        elif token.startswith("`"):
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(10)
        elif token.startswith("["):
            link_match = re.match(r"\[([^\]]+)\]\(([^\)]+)\)", token)
            if link_match:
                paragraph.add_run(link_match.group(1))
        pos = match.end()

    if pos < len(text):
        paragraph.add_run(text[pos:])


# ---------------------------------------------------------------------------
# Procesador principal
# ---------------------------------------------------------------------------

def parse_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Extrae una tabla Markdown empezando en lines[start]. Devuelve filas + nuevo índice."""
    rows = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        rows.append(row)
        i += 1
    # filtrar separador (---|---)
    rows = [r for r in rows if not all(re.fullmatch(r":?-+:?", c) for c in r)]
    return rows, i


def add_table(doc: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Light Grid Accent 1"
    table.autofit = True

    for r_idx, row in enumerate(rows):
        for c_idx, cell_text in enumerate(row):
            cell = table.rows[r_idx].cells[c_idx]
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.line_spacing = 1.15
            p.paragraph_format.space_after = Pt(0)
            add_runs_with_inline(p, cell_text)
            if r_idx == 0:
                for run in p.runs:
                    run.bold = True
                set_cell_shading(cell, "D9E1F2")
    # espacio después
    doc.add_paragraph()


def add_code_block(doc: Document, code_lines: list[str]) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.left_indent = Cm(0.5)
    run = p.add_run("\n".join(code_lines))
    run.font.name = "Consolas"
    run.font.size = Pt(9)


def add_quote(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(1.0)
    p.paragraph_format.right_indent = Cm(0.5)
    p.paragraph_format.line_spacing = 1.3
    run_marker = p.add_run("")
    add_runs_with_inline(p, text)
    for run in p.runs:
        run.italic = True


def convert(md_path: Path, docx_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    doc = Document()
    setup_styles(doc)

    section = doc.sections[0]
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(2.5)

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Línea vacía
        if not stripped:
            i += 1
            continue

        # Separador horizontal
        if stripped == "---":
            i += 1
            continue

        # Bloque de código ``` ... ```
        if stripped.startswith("```"):
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            add_code_block(doc, code)
            i += 1
            continue

        # Encabezados
        h_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if h_match:
            level = len(h_match.group(1))
            heading = doc.add_heading(level=min(level, 4))
            heading.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
            add_runs_with_inline(heading, h_match.group(2).strip())
            i += 1
            continue

        # Bloque cita acumulado
        if stripped.startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip()[1:].strip())
                i += 1
            add_quote(doc, " ".join(quote_lines))
            continue

        # Tabla
        if stripped.startswith("|") and i + 1 < len(lines) and "|" in lines[i + 1]:
            rows, new_i = parse_table(lines, i)
            add_table(doc, rows)
            i = new_i
            continue

        # Lista con guion
        if stripped.startswith("- "):
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.line_spacing = 1.3
            add_runs_with_inline(p, stripped[2:])
            i += 1
            continue

        # Lista numerada
        num_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if num_match:
            p = doc.add_paragraph(style="List Number")
            p.paragraph_format.line_spacing = 1.3
            add_runs_with_inline(p, num_match.group(2))
            i += 1
            continue

        # Párrafo normal — acumular líneas consecutivas
        paragraph_lines = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not (
            lines[i].strip().startswith(("#", "|", "-", ">", "```"))
            or re.match(r"^\d+\.\s", lines[i].strip())
        ):
            paragraph_lines.append(lines[i].strip())
            i += 1

        p = doc.add_paragraph()
        add_runs_with_inline(p, " ".join(paragraph_lines))

    doc.save(docx_path)
    print(f"OK -> {docx_path}")


if __name__ == "__main__":
    if not MD_PATH.exists():
        print(f"ERROR: no existe {MD_PATH}", file=sys.stderr)
        sys.exit(1)
    convert(MD_PATH, DOCX_PATH)

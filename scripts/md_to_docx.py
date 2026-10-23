"""把 03-作品说明文档.md 转成 docx（宋体四号 = 12pt，1.5 倍行距，比赛要求）。"""
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

MD = Path("docs/参赛材料/03-作品说明文档.md")
OUT = Path("docs/参赛材料/03-作品说明文档.docx")


def set_font(run, name="宋体", size=12, bold=False, italic=False):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def add_rich(doc, text, size=12, style=None, bullet=False):
    p = doc.add_paragraph(style=style)
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            r = p.add_run(part[2:-2])
            set_font(r, "宋体", size, bold=True)
        else:
            r = p.add_run(part.replace("`", ""))
            set_font(r, "宋体", size)
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    return p


def main():
    lines = MD.read_text(encoding="utf-8").splitlines()
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(12)
    try:
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    except Exception:
        pass

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue

        # 代码块
        if s.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            p = doc.add_paragraph()
            r = p.add_run("\n".join(code))
            set_font(r, "Consolas", 9)
            p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            p = doc.add_heading(level=min(level, 3))
            r = p.add_run(text)
            set_font(r, "宋体", 18 if level == 1 else 15 if level == 2 else 13, bold=True)
            p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
            i += 1
            continue

        # 表格
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                raw = lines[i].strip()
                i += 1
                if re.match(r"^\|[\s:|-]+\|$", raw):
                    continue
                rows.append([c.strip() for c in raw.strip("|").split("|")])
            if rows:
                ncol = max(len(r) for r in rows)
                t = doc.add_table(rows=len(rows), cols=ncol)
                t.style = "Table Grid"
                for ri, row in enumerate(rows):
                    for ci in range(ncol):
                        cell = t.cell(ri, ci)
                        cell.text = ""
                        p = cell.paragraphs[0]
                        r = p.add_run(row[ci] if ci < len(row) else "")
                        set_font(r, "宋体", 10.5, bold=(ri == 0))
            continue

        # 列表
        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            add_rich(doc, m.group(1), 12, style="List Bullet")
            i += 1
            continue
        m = re.match(r"^\d+\.\s+(.*)$", s)
        if m:
            add_rich(doc, m.group(1), 12, style="List Number")
            i += 1
            continue

        # 引用
        if s.startswith(">"):
            p = add_rich(doc, s.lstrip("> ").strip(), 12)
            for r in p.runs:
                r.font.italic = True
            p.paragraph_format.left_indent = Cm(1)
            i += 1
            continue

        # 图片
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", s)
        if m:
            alt, rel = m.group(1), m.group(2)
            img_path = (MD.parent / rel).resolve()
            if img_path.exists():
                doc.add_picture(str(img_path), width=Cm(15))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                if alt:
                    p = doc.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    r = p.add_run(alt)
                    set_font(r, "宋体", 10.5)
            else:
                add_rich(doc, f"[图片缺失: {rel}]", 10.5)
            i += 1
            continue

        # 普通段落
        add_rich(doc, s, 12)
        i += 1

    doc.save(OUT)
    print(f"Saved {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

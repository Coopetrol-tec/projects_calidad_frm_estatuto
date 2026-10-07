import json
import re
from pathlib import Path

import pymupdf

PARSER_VERSION = 3
CHAPTER_RE = re.compile(r"^CAP[ÍI]TULO\s+([IVXLCDM]+)\b", re.I)
ARTICLE_RE = re.compile(r"^Art[íi]culo\s+(\d+)\b", re.I)
PARAGRAPH_RE = re.compile(r"^PAR[ÁA]GRAFO(?:\s+(\d+))?\b", re.I)
NUMERAL_RE = re.compile(r"^(\d+)\.$")
LITERAL_RE = re.compile(r"^([A-Z])\.$")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\x00", " ")).strip()


def _line_records(page):
    data = page.get_text("dict")
    lines = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            text = _clean(" ".join(s.get("text", "") for s in spans))
            bbox = [
                min(s["bbox"][0] for s in spans),
                min(s["bbox"][1] for s in spans),
                max(s["bbox"][2] for s in spans),
                max(s["bbox"][3] for s in spans),
            ]
            lines.append({
                "text": text,
                "bbox": bbox,
                "size": max(float(s.get("size", 0)) for s in spans),
            })
    return sorted(lines, key=lambda x: (round(x["bbox"][1], 1), x["bbox"][0]))


def _build_markers(doc):
    markers = []
    seen_chapters = set()
    seen_articles = set()
    article_candidates = []

    for page_index, page in enumerate(doc):
        page_number = page_index + 1
        lines = _line_records(page)

        page_text = page.get_text("text")
        for match in re.finditer(r"(?im)^\s*Art[íi]culo\s+(\d+)\b", page_text):
            article_candidates.append((int(match.group(1)), page_number))

        for i, line in enumerate(lines):
            text = line["text"]
            if not text or page_number <= 3:
                continue

            chapter = CHAPTER_RE.match(text)
            if chapter:
                roman = chapter.group(1).upper()
                if roman not in seen_chapters:
                    title = ""
                    for nxt in lines[i + 1:i + 5]:
                        nt = nxt["text"]
                        if not nt or nt == "Estatuto" or re.fullmatch(r"\d+", nt):
                            continue
                        if CHAPTER_RE.match(nt) or ARTICLE_RE.match(nt) or PARAGRAPH_RE.match(nt):
                            continue
                        title = nt
                        break
                    markers.append({
                        "kind": "chapter",
                        "page": page_number,
                        "bbox": line["bbox"],
                        "value": roman,
                        "title": title,
                    })
                    seen_chapters.add(roman)
                continue

            article = ARTICLE_RE.match(text)
            if article:
                number = int(article.group(1))
                if number not in seen_articles:
                    markers.append({
                        "kind": "article",
                        "page": page_number,
                        "bbox": line["bbox"],
                        "value": str(number),
                        "title": "",
                    })
                    seen_articles.add(number)
                continue

            paragraph = PARAGRAPH_RE.match(text)
            if paragraph:
                markers.append({
                    "kind": "paragraph",
                    "page": page_number,
                    "bbox": line["bbox"],
                    "value": paragraph.group(1) or "TRANSITORIO",
                    "title": "",
                })
                continue

            numeral = NUMERAL_RE.match(text)
            if numeral:
                markers.append({
                    "kind": "numeral",
                    "page": page_number,
                    "bbox": line["bbox"],
                    "value": numeral.group(1),
                    "title": "",
                })
                continue

            literal = LITERAL_RE.match(text)
            if literal:
                markers.append({
                    "kind": "literal",
                    "page": page_number,
                    "bbox": line["bbox"],
                    "value": literal.group(1).upper(),
                    "title": "",
                })

    # Coordinate fallback for article headings that the line parser missed.
    for number, page_number in article_candidates:
        if number in seen_articles:
            continue
        page = doc[page_number - 1]
        rects = []
        for probe in (
            f"Artículo {number}",
            f"ARTÍCULO {number}",
            f"Articulo {number}",
            f"ARTICULO {number}",
        ):
            rects = page.search_for(probe)
            if rects:
                break
        if rects:
            rect = sorted(rects, key=lambda r: (r.y0, r.x0))[0]
            markers.append({
                "kind": "article",
                "page": page_number,
                "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                "value": str(number),
                "title": "",
            })
            seen_articles.add(number)

    priority = {"chapter": 0, "article": 1, "paragraph": 2, "numeral": 3, "literal": 4}
    markers.sort(key=lambda m: (
        m["page"],
        round(m["bbox"][1], 1),
        priority[m["kind"]],
        m["bbox"][0],
    ))
    return markers


def _merge_same_baseline(lines, slot_start, slot_end, page_width):
    """Build tight clickable rectangles that follow the actual printed text.

    Lines that visually share a baseline (for example `2.` + its sentence) are
    merged horizontally. Long clauses remain as multiple line-sized hit areas,
    avoiding a large rectangle that also covers the next numeral or paragraph.
    """
    if not lines:
        return []

    groups = []
    for line in sorted(lines, key=lambda x: (x["bbox"][1], x["bbox"][0])):
        y0 = line["bbox"][1]
        if not groups or abs(y0 - groups[-1][0]["bbox"][1]) > 2.2:
            groups.append([line])
        else:
            groups[-1].append(line)

    regions = []
    for group in groups:
        x0 = min(line["bbox"][0] for line in group)
        y0 = min(line["bbox"][1] for line in group)
        x1 = max(line["bbox"][2] for line in group)
        y1 = max(line["bbox"][3] for line in group)

        # Small padding improves clickability while keeping the hit area on text.
        rx0 = max(4.0, x0 - 2.5)
        ry0 = max(slot_start, y0 - 1.3)
        rx1 = min(page_width - 4.0, x1 + 2.5)
        ry1 = min(slot_end, y1 + 1.3)

        # Very tightly spaced list markers can have overlapping font boxes.
        # Cap at the logical slot boundary so one option never covers the next.
        if ry1 <= ry0:
            ry1 = min(slot_end, ry0 + 5.0)
        if rx1 > rx0 and ry1 > ry0:
            regions.append([rx0, ry0, rx1, ry1])

    return regions


def _union_region(regions):
    if not regions:
        return [0, 0, 0, 0]
    return [
        min(r[0] for r in regions),
        min(r[1] for r in regions),
        max(r[2] for r in regions),
        max(r[3] for r in regions),
    ]


def _build_items(doc, markers):
    """Build selectable statute items, including continuations across PDF pages.

    Every detected marker starts one logical item. If the text belonging to that
    marker continues onto the next page before another marker appears, one or
    more continuation segments are created with the same ``logical_id`` and the
    same statutory reference. This makes continuation text searchable and
    clickable while preserving the page where the user actually clicked.
    """
    items = []
    current_chapter = ""
    current_article = ""
    current_paragraph = ""
    current_numeral = ""
    current_literal = ""

    clean_markers = []
    seen = set()
    for marker in markers:
        key = (
            marker["kind"],
            marker["page"],
            marker["value"],
            round(marker["bbox"][1], 0),
            round(marker["bbox"][0], 0),
        )
        if key not in seen:
            seen.add(key)
            clean_markers.append(marker)

    # Continuations can live on pages without any marker of their own, so cache
    # every interactive page instead of marker pages only.
    lines_cache = {
        page_number: _line_records(doc[page_number - 1])
        for page_number in range(4, doc.page_count + 1)
    }

    marker_contexts = []
    for marker in clean_markers:
        kind = marker["kind"]
        value = marker["value"]

        if kind == "chapter":
            current_chapter = f"Capítulo {value}"
            if marker.get("title"):
                current_chapter += f" · {marker['title']}"
            current_article = ""
            current_paragraph = ""
            current_numeral = ""
            current_literal = ""
        elif kind == "article":
            current_article = value
            current_paragraph = ""
            current_numeral = ""
            current_literal = ""
        elif kind == "paragraph":
            current_paragraph = value
            current_numeral = ""
            current_literal = ""
        elif kind == "numeral":
            current_numeral = value
            current_literal = ""
        elif kind == "literal":
            current_literal = value

        marker_contexts.append({
            "marker": marker,
            "capitulo": current_chapter,
            "articulo": current_article,
            "paragrafo": current_paragraph,
            "numeral": current_numeral,
            "literal": current_literal,
        })

    def label_for(ctx):
        kind = ctx["marker"]["kind"]
        if kind == "chapter":
            return ctx["capitulo"]
        if kind == "article":
            return f"Artículo {ctx['articulo']}"
        if kind == "paragraph":
            return f"Artículo {ctx['articulo']} · Parágrafo {ctx['paragrafo']}"
        if kind == "numeral":
            return f"Artículo {ctx['articulo']} · Numeral {ctx['numeral']}"
        return f"Artículo {ctx['articulo']} · Literal {ctx['literal']}"

    def segment_lines(page_number, y_start, y_end):
        selected = []
        for line in lines_cache.get(page_number, []):
            ly = line["bbox"][1]
            if y_start <= ly < y_end:
                text = line["text"]
                if text == "Estatuto" or re.fullmatch(r"\d+", text):
                    continue
                selected.append(line)
        return selected

    for idx, ctx in enumerate(marker_contexts):
        marker = ctx["marker"]
        marker_page = marker["page"]
        if marker_page < 4:
            continue

        next_marker = marker_contexts[idx + 1]["marker"] if idx + 1 < len(marker_contexts) else None
        last_page = next_marker["page"] if next_marker else doc.page_count
        logical_id = f"logical-{idx + 1}"
        base_label = label_for(ctx)

        for page_number in range(marker_page, last_page + 1):
            page = doc[page_number - 1]
            is_continuation = page_number != marker_page

            if is_continuation:
                slot_start = 0.0
                line_start = 0.0
            else:
                marker_y = marker["bbox"][1]
                slot_start = max(0.0, marker_y - 1.6)
                line_start = marker_y - 1.0

            slot_end = page.rect.height - 20.0
            if next_marker and page_number == next_marker["page"]:
                slot_end = min(slot_end, max(slot_start, next_marker["bbox"][1] - 0.7))

            if slot_end <= slot_start + 0.5:
                continue

            selected_lines = segment_lines(page_number, line_start, slot_end)

            if not selected_lines:
                if is_continuation:
                    continue
                selected_lines = [{"text": "", "bbox": marker["bbox"]}]

            regions = _merge_same_baseline(
                selected_lines,
                slot_start,
                slot_end,
                float(page.rect.width),
            )
            if not regions:
                if is_continuation:
                    continue
                x0, y0, x1, y1 = marker["bbox"]
                regions = [[x0 - 2, y0 - 1, x1 + 2, y1 + 1]]

            snippet = _clean(" ".join(line.get("text", "") for line in selected_lines))[:1000]
            label = base_label + (" · continuación" if is_continuation else "")

            items.append({
                "id": len(items) + 1,
                "logical_id": logical_id,
                "continuation": is_continuation,
                "source_page": marker_page,
                "kind": marker["kind"],
                "page": page_number,
                "regions": regions,
                "region": _union_region(regions),
                "capitulo": ctx["capitulo"],
                "articulo": ctx["articulo"],
                "paragrafo": ctx["paragrafo"],
                "numeral": ctx["numeral"],
                "literal": ctx["literal"],
                "label": label,
                "snippet": snippet,
            })

    return items


def process_statute_pdf(pdf_path: str, json_output_path: str, images_dir: str):
    pdf_path = Path(pdf_path)
    json_path = Path(json_output_path)
    images_path = Path(images_dir)
    images_path.mkdir(parents=True, exist_ok=True)

    for old in images_path.glob("page_*.png"):
        old.unlink(missing_ok=True)

    doc = pymupdf.open(pdf_path)
    markers = _build_markers(doc)
    items = _build_items(doc, markers)

    pages = []
    zoom = 1.25
    matrix = pymupdf.Matrix(zoom, zoom)
    # Pages 1-3 are cover / table of contents. The interactive viewer starts at page 4.
    for page_index in range(3, doc.page_count):
        page = doc[page_index]
        image_name = f"page_{page_index + 1:03d}.png"
        image_path = images_path / image_name
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(str(image_path))
        pages.append({
            "number": page_index + 1,
            "width": int(round(page.rect.width * zoom)),
            "height": int(round(page.rect.height * zoom)),
            "pdf_width": float(page.rect.width),
            "pdf_height": float(page.rect.height),
            "image": f"generated/pages/{image_name}",
        })

    article_count = len({
        item["articulo"]
        for item in items
        if item["kind"] == "article" and item["articulo"]
    })
    payload = {
        "parser_version": PARSER_VERSION,
        "pages": pages,
        "items": items,
        "article_count": article_count,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    doc.close()
    return payload

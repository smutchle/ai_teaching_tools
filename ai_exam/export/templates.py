"""Quarto `.qmd` source builders for the Phase 4 export bundle.

Each builder takes Pydantic models from `models.py` and returns the `.qmd`
content as a string. Quarto handles the rendering to PDF / DOCX / LaTeX; we
just produce the source.

Rendering targets are declared in each document's YAML frontmatter so a single
`quarto render <file>.qmd` produces every format we want. Per the user's call,
we emit PDF, DOCX, and LaTeX for the student-facing exam and answer key;
the audit report is PDF + DOCX only (no need for a .tex source).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from models import (
    CLO,
    CourseSpec,
    ExamAudit,
    ExamDraft,
    ExamSpec,
    Item,
    ItemStatus,
    ItemType,
    ItemVariant,
    Topic,
)


# ---- YAML frontmatter ----------------------------------------------------

# `from: markdown+fancy_lists` tells Pandoc to recognize `(a)`, `A.`, `(A)`,
# `1.`, etc. as list-item markers — in addition to plain bullets. Without
# this extension, multi-part question stems like "(a) Compute X." render
# as inline text, not a list. Pair with our _normalize_markdown pass which
# guarantees blank lines before list items.

_FRONTMATTER_ALL_FORMATS = """---
title: "{title}"
subtitle: "{subtitle}"
date: "{date}"
lang: en
from: markdown+fancy_lists
format:
  pdf:
    pdf-engine: xelatex
    geometry:
      - margin=1in
    documentclass: article
    fontsize: 11pt
  docx: default
  latex: default
---

"""

_FRONTMATTER_REPORT = """---
title: "{title}"
subtitle: "{subtitle}"
date: "{date}"
lang: en
from: markdown+fancy_lists
format:
  pdf:
    pdf-engine: xelatex
    geometry:
      - margin=1in
    documentclass: article
    fontsize: 11pt
    toc: true
  docx:
    toc: true
---

"""


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---- meta-text safety ----------------------------------------------------

# LaTeX command names are `\` + one or more letters. Used by the meta-text
# escaper to wrap command names that appear in prose so the LaTeX engine
# doesn't try to *execute* them when the surrounding text isn't math mode.
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+")


def _safe_meta_text(s: str) -> str:
    """Wrap LaTeX command tokens in inline-code backticks ONLY in the parts of
    the string that are not already inside `$...$` math delimiters.

    Used for prose fields (accessibility_notes, SME provenance rationale) that
    *discuss* LaTeX commands by name — e.g., 'Display equations use \\dfrac
    for readable visual scaling.' Without this, pandoc passes the bare
    `\\dfrac` into the .tex output, xelatex tries to execute it in text mode,
    and the whole PDF render fails.

    Stems, answer keys, and rubrics are NOT passed through this — they are
    allowed to mix prose and math freely and render correctly through Quarto
    because their math content lives inside `$...$` regions.
    """
    parts = s.split("$")
    # Even-index parts are outside math; odd-index parts are inside math.
    for i in range(0, len(parts), 2):
        parts[i] = _LATEX_CMD_RE.sub(lambda m: f"`{m.group(0)}`", parts[i])
    return "$".join(parts)


# A list marker per Pandoc's `fancy_lists` extension is any of:
#   - bullet:        - foo, * foo, + foo
#   - lowercase:     a) foo, a. foo, (a) foo
#   - uppercase:     A) foo, A. foo, (A) foo
#   - numeric:       1) foo, 1. foo  (capped at 2 digits to avoid catching years)
# The marker must be at line start (leading whitespace OK) and followed by
# at least one whitespace character.
_LIST_MARKER_RE = re.compile(
    r"^\s*(?:[-*+]|\(?[a-zA-Z]\)?[.)]|\d{1,2}[.)])\s"
)

# SME-style sub-question marker wrapped in inline bold or italic at line
# start: `**(a)** foo`, `__a.__ foo`, `*(A)* foo`. We strip the wrapping
# so Pandoc parses the marker as a fancy-list item instead of inline
# bold text glued to the paragraph above.
_FORMATTED_MARKER_RE = re.compile(
    r"^(\s*)(?:\*\*|__|\*|_)(\(?[a-zA-Z]\)?[.)]?)(?:\*\*|__|\*|_)\s+",
    re.MULTILINE,
)

# Strip a leading letter-prefix the SME sometimes embeds inside MCQ option
# text (e.g. "A. ...", "B) ...", "C: ..."). Without this, the rendered
# option doubles up: "A. A) ..." because the template adds its own letter.
# Matches an initial A-H followed by . ) : or - then whitespace. Won't
# match plain words like "A note..." (no delimiter).
_LEADING_LETTER_PREFIX = re.compile(r"^\s*[A-Ha-h]\s*[.\):\-]\s+")


def _strip_option_letter_prefix(opt: str) -> str:
    return _LEADING_LETTER_PREFIX.sub("", opt, count=1)


# Letter-list marker: either `(a)` parenthesized or `a.`/`a)` with a
# trailing delimiter, in either case followed by whitespace. We canonicalize
# every match to `A.  ` (uppercase letter + period + TWO spaces, the form
# Pandoc fancy_lists requires for capital-letter markers).
_LETTER_LIST_MARKER_RE = re.compile(
    r"^(\s*)(?:\((?P<paren>[a-zA-Z])\)|(?P<bare>[a-zA-Z])[.)])\s+",
    re.MULTILINE,
)


def _normalize_markdown(text: str) -> str:
    """Defensive normalization of SME-authored markdown for Quarto rendering.

    Three passes, in order:

    1. **Strip inline formatting around sub-question markers** —
       `**(a)** Calculate Q.` becomes `(a) Calculate Q.` so Pandoc parses
       it as a list item instead of inline bold text.

    2. **Canonicalize letter markers to uppercase `A.` form** — the SME
       writes multi-part questions with `(a) (b) (c)` while MCQ options
       use `A. B. C.`. The two-style mix looks inconsistent across an
       exam. We unify everything on `A.  ` (capital + period + two spaces,
       which is the form Pandoc fancy_lists actually requires for
       capital-letter markers; without two spaces it gets confused with
       initials like "B. Williams").

    3. **Insert blank lines before list items** that follow non-blank,
       non-list content — CommonMark requires it. Without the blank line
       the marker renders glued to the paragraph above.

    The Quarto frontmatter enables `+fancy_lists` so the canonical
    `A.  foo` style parses as an ordered list across PDF, DOCX, HTML, and
    LaTeX. The SME persona discourages bold-wrapped markers in the first
    place; this is the defense-in-depth catch.
    """
    # Pass 1: strip inline formatting from markers.
    text = _FORMATTED_MARKER_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)} ", text,
    )

    # Pass 2: canonicalize every letter marker to uppercase `A.  ` form.
    def _canon(m: re.Match[str]) -> str:
        letter = (m.group("paren") or m.group("bare")).upper()
        return f"{m.group(1)}{letter}.  "
    text = _LETTER_LIST_MARKER_RE.sub(_canon, text)

    # Pass 3: enforce blank lines before list items.
    out: list[str] = []
    prev_is_content = False  # non-blank AND not a list item
    for line in text.splitlines():
        is_list = bool(_LIST_MARKER_RE.match(line))
        is_blank = not line.strip()
        if is_list and prev_is_content:
            out.append("")
        out.append(line)
        prev_is_content = (not is_blank) and (not is_list)
    return "\n".join(out)


# Back-compat alias — older call sites use `_fix_bullet_spacing`. Keep
# them working; both names route through the unified normalizer.
def _fix_bullet_spacing(text: str) -> str:
    return _normalize_markdown(text)


def _blockquote(text: str) -> str:
    """Wrap each line of `text` in a markdown blockquote.

    Blank source lines become bare `>` markers so Pandoc keeps the blockquote
    contiguous across paragraphs and lists inside it. Combine with
    `_fix_bullet_spacing` first when the text might contain bullets that
    follow paragraph content."""
    lines = text.splitlines() or [""]
    return "\n".join(("> " + line) if line.strip() else ">" for line in lines)


# ---- shared item rendering ----------------------------------------------

def _item_heading(item: Item, n: int, *, show_answer: bool) -> str:
    """Question heading line: number, point value, type, Bloom level."""
    bits = [f"## Question {n} ({item.points} pts)",
            "",
            f"*Type: {item.type.value.replace('_', ' ')} · "
            f"Bloom: {item.bloom_level.value} · "
            f"Difficulty: {item.difficulty_est.value}*",
            ""]
    return "\n".join(bits)


def _render_mcq_options(item: Item) -> str:
    if not item.options:
        return ""
    # Pandoc's fancy_lists requires TWO spaces after a capital-letter
    # marker so "A.  foo" doesn't get confused with initials like
    # "B. Williams". With a single space, every option mashes together
    # into one paragraph. The two spaces are the smallest reliable cue.
    out: list[str] = [""]
    letters = "ABCDEFGH"
    for i, opt in enumerate(item.options):
        out.append(f"{letters[i]}.  {_strip_option_letter_prefix(opt)}")
    out.append("")
    return "\n".join(out)


def _render_item_student_view(item: Item, n: int) -> str:
    """Student-facing rendering: stem + options if MCQ; no answer or rationale."""
    chunks: list[str] = [
        _item_heading(item, n, show_answer=False),
        _fix_bullet_spacing(item.stem),
        "",
    ]
    if item.type == ItemType.MCQ:
        chunks.append(_render_mcq_options(item))
    if item.type in {ItemType.SHORT_ANSWER, ItemType.PROBLEM, ItemType.DERIVATION,
                     ItemType.DATA_INTERP}:
        chunks.append("> *Write your answer in the space provided.*")
        chunks.append("")
    return "\n".join(chunks)


def _render_item_answer_key(item: Item, n: int, course_spec: CourseSpec) -> str:
    chunks: list[str] = [_item_heading(item, n, show_answer=True)]
    chunks.append("**Stem:**")
    chunks.append("")
    chunks.append(_fix_bullet_spacing(item.stem))
    chunks.append("")
    if item.type == ItemType.MCQ and item.options:
        chunks.append(_render_mcq_options(item))
    chunks.append("**Answer:**")
    chunks.append("")
    chunks.append(_fix_bullet_spacing(item.answer_key))
    chunks.append("")

    if item.clo_refs:
        chunks.append("**Learning outcomes measured:**")
        chunks.append("")
        for clo_id in item.clo_refs:
            clo = _lookup_clo(course_spec, clo_id)
            if clo is not None:
                chunks.append(f"- *{clo_id}*: {clo.text}")
            else:
                chunks.append(f"- *{clo_id}* (not found in CourseSpec)")
        chunks.append("")

    if item.topic_refs:
        topic_names = [
            (_lookup_topic(course_spec, tid).name if _lookup_topic(course_spec, tid) else tid)
            for tid in item.topic_refs
        ]
        chunks.append(f"**Topic(s):** {', '.join(topic_names)}")
        chunks.append("")

    if item.source_refs:
        chunks.append("**Grounded in:**")
        chunks.append("")
        for ref in item.source_refs:
            loc = f" — {ref.locator}" if ref.locator else ""
            chunks.append(f"- {ref.source_doc}{loc} (chunk `{ref.chunk_id}`)")
        chunks.append("")

    if item.accessibility_notes:
        chunks.append("**Accessibility notes:**")
        chunks.append("")
        for note in item.accessibility_notes:
            chunks.append(f"- {_safe_meta_text(note)}")
        chunks.append("")

    # `rationale` doesn't exist on Item — the SME's rationale lives in
    # provenance. We surface the SME `proposed` rationale if present.
    sme_proposed = next(
        (p for p in item.provenance if p.agent == "sme" and p.action == "proposed"),
        None,
    )
    if sme_proposed and sme_proposed.rationale:
        chunks.append(
            f"**SME rationale (from provenance):** "
            f"{_safe_meta_text(sme_proposed.rationale)}"
        )
        chunks.append("")
    return "\n".join(chunks)


def _lookup_clo(course_spec: CourseSpec, clo_id: str) -> CLO | None:
    for c in course_spec.clos:
        if c.id == clo_id:
            return c
    return None


def _lookup_topic(course_spec: CourseSpec, topic_id: str) -> Topic | None:
    for t in course_spec.topics:
        if t.id == topic_id:
            return t
    return None


def _render_item_rubric(item: Item, n: int) -> str | None:
    if not item.rubric:
        return None
    chunks: list[str] = [
        _item_heading(item, n, show_answer=True),
        "**Stem:**",
        "",
        _fix_bullet_spacing(item.stem),
        "",
        "**Rubric:**",
        "",
        _fix_bullet_spacing(item.rubric),
        "",
    ]
    return "\n".join(chunks)


# ---- exam.qmd ------------------------------------------------------------

def build_exam_qmd(draft: ExamDraft, exam_spec: ExamSpec) -> str:
    survivors = _surviving_items(draft)
    fm = _FRONTMATTER_ALL_FORMATS.format(
        title=f"{exam_spec.exam_type.value.title()} Exam",
        subtitle=(
            f"{len(survivors)} questions · {sum(i.points for i in survivors)} points · "
            f"{exam_spec.time_budget_minutes} minutes"
        ),
        date=_today(),
    )
    body: list[str] = [fm, "## Instructions", "",
                       f"- Total points: **{sum(i.points for i in survivors)}**",
                       f"- Time budget: **{exam_spec.time_budget_minutes} minutes**",
                       "- Answer all questions in the spaces provided.",
                       "",
                       "---", ""]
    for n, item in enumerate(survivors, start=1):
        body.append(_render_item_student_view(item, n))
        body.append("---")
        body.append("")
    return "\n".join(body)


# ---- answer_key.qmd ------------------------------------------------------

def build_answer_key_qmd(
    draft: ExamDraft, exam_spec: ExamSpec, course_spec: CourseSpec
) -> str:
    survivors = _surviving_items(draft)
    fm = _FRONTMATTER_ALL_FORMATS.format(
        title=f"{exam_spec.exam_type.value.title()} Exam — Answer Key",
        subtitle="Instructor copy. Do not distribute to students.",
        date=_today(),
    )
    body: list[str] = [fm]
    for n, item in enumerate(survivors, start=1):
        body.append(_render_item_answer_key(item, n, course_spec))
        body.append("---")
        body.append("")
    return "\n".join(body)


# ---- instructor_notes.qmd ------------------------------------------------

# Short cognitive-operation gloss per Bloom level. Used in the instructor
# narrative to explain what the cognitive demand of an item actually is, so
# the reader does not need to keep Bloom's taxonomy in their head.
_BLOOM_GLOSS: dict[str, str] = {
    "remember": "recall a fact, definition, or named relationship",
    "understand": "explain a concept in their own words",
    "apply": "use a procedure or formula in a new context",
    "analyze": "decompose a situation and reason about its parts",
    "evaluate": "judge between alternatives against stated criteria",
    "create": "produce a novel solution, derivation, or design",
}


def _render_instructor_note(item: Item, n: int, course_spec: CourseSpec) -> str:
    """Render one item's instructor narrative: what it measures, at what level,
    on what topic, grounded where. Synthesized from existing data — no LLM."""
    chunks: list[str] = [f"## Question {n} — *{item.id}* — {item.points} pts", ""]

    # Show the stem so the reviewer does not have to cross-reference.
    chunks.append(_blockquote(_fix_bullet_spacing(item.stem)))
    chunks.append("")

    # CLO line: resolve each id to its full text so the reader does not have
    # to chase ids back to the course spec.
    clos_resolved: list[str] = []
    for clo_id in item.clo_refs:
        clo = _lookup_clo(course_spec, clo_id)
        if clo is not None:
            clos_resolved.append(f"**{clo_id}** — *{clo.text}*")
        else:
            clos_resolved.append(f"**{clo_id}** (not found in CourseSpec)")
    if clos_resolved:
        chunks.append("**Measures:** " + "; ".join(clos_resolved))
        chunks.append("")

    bloom_gloss = _BLOOM_GLOSS.get(item.bloom_level.value, "")
    if bloom_gloss:
        chunks.append(
            f"**Cognitive level:** {item.bloom_level.value} "
            f"— students must {bloom_gloss}."
        )
    else:
        chunks.append(f"**Cognitive level:** {item.bloom_level.value}")
    chunks.append("")

    if item.topic_refs:
        topic_names = [
            (_lookup_topic(course_spec, tid).name if _lookup_topic(course_spec, tid) else tid)
            for tid in item.topic_refs
        ]
        chunks.append(f"**Topic(s):** {', '.join(topic_names)}")
        chunks.append("")

    chunks.append(
        f"**Difficulty target:** {item.difficulty_est.value} · "
        f"**Item type:** {item.type.value.replace('_', ' ')}"
    )
    chunks.append("")

    if item.source_refs:
        srcs = [
            f"`{ref.source_doc}`" + (f" — {ref.locator}" if ref.locator else "")
            for ref in item.source_refs
        ]
        chunks.append(f"**Grounded in:** {', '.join(srcs)}")
        chunks.append("")

    if item.accessibility_notes:
        chunks.append("**Accessibility considerations:**")
        chunks.append("")
        for note in item.accessibility_notes:
            chunks.append(f"- {_safe_meta_text(note)}")
        chunks.append("")

    return "\n".join(chunks)


def build_instructor_notes_qmd(
    draft: ExamDraft, exam_spec: ExamSpec, course_spec: CourseSpec
) -> str:
    """Per-question pedagogical narrative: what each item accomplishes and ties
    back to. Synthesized from existing data — no LLM call needed."""
    survivors = _surviving_items(draft)
    fm = _FRONTMATTER_ALL_FORMATS.format(
        title=f"{exam_spec.exam_type.value.title()} Exam — Instructor Notes",
        subtitle="Per-question pedagogical narrative. Instructor / reviewer copy.",
        date=_today(),
    )
    body: list[str] = [
        fm,
        "## About this document",
        "",
        "Each section below explains what the corresponding exam question "
        "measures, at what cognitive level, on which topic, and against "
        "which course materials. Use this for departmental review, grade "
        "appeals, and exam-design defense.",
        "",
        "---",
        "",
    ]
    for n, item in enumerate(survivors, start=1):
        body.append(_render_instructor_note(item, n, course_spec))
        body.append("---")
        body.append("")
    return "\n".join(body)


# ---- rubrics.qmd ---------------------------------------------------------

def build_rubrics_qmd(draft: ExamDraft, exam_spec: ExamSpec) -> str | None:
    """Return the rubrics .qmd, or None if no item has a rubric."""
    survivors = _surviving_items(draft)
    with_rubrics = [it for it in survivors if it.rubric]
    if not with_rubrics:
        return None
    fm = _FRONTMATTER_ALL_FORMATS.format(
        title=f"{exam_spec.exam_type.value.title()} Exam — Rubrics",
        subtitle="Scoring guidance for instructor / TA use.",
        date=_today(),
    )
    body: list[str] = [fm]
    # Re-number using the survivor index so rubric headings line up with the exam.
    for n, item in enumerate(survivors, start=1):
        if not item.rubric:
            continue
        rendered = _render_item_rubric(item, n)
        if rendered:
            body.append(rendered)
            body.append("---")
            body.append("")
    return "\n".join(body)


# ---- exam_report.qmd (from ExamAudit) ------------------------------------

def build_audit_report_qmd(
    draft: ExamDraft,
    audit: ExamAudit,
    exam_spec: ExamSpec,
) -> str:
    fm = _FRONTMATTER_REPORT.format(
        title="Exam Design Report",
        subtitle="Blueprint coverage, Bloom distribution, difficulty curve, and audit findings.",
        date=_today(),
    )
    body: list[str] = [fm, "## Summary", "", audit.report.summary, ""]

    body.append("## Bloom Distribution")
    body.append("")
    body.append("| Bloom Level | Items | Points |")
    body.append("|:------------|------:|-------:|")
    for stat in audit.report.bloom_distribution:
        body.append(f"| {stat.bloom_level.value} | {stat.item_count} | {stat.points} |")
    body.append("")

    body.append("## Difficulty Curve")
    body.append("")
    body.append("| Difficulty | Actual count | Target ratio |")
    body.append("|:-----------|-------------:|-------------:|")
    dc = audit.report.difficulty_curve
    body.append(f"| easy   | {dc.easy_count}   | {dc.target_easy_ratio:.2f} |")
    body.append(f"| medium | {dc.medium_count} | {dc.target_medium_ratio:.2f} |")
    body.append(f"| hard   | {dc.hard_count}   | {dc.target_hard_ratio:.2f} |")
    body.append("")

    body.append("## CLO Coverage")
    body.append("")
    body.append("| CLO | Items | Points | Covered |")
    body.append("|:----|------:|-------:|:-------:|")
    for cov in audit.report.clo_coverage:
        body.append(f"| {cov.clo_id} | {cov.item_count} | {cov.points} | "
                    f"{'✓' if cov.is_covered else '✗'} |")
    body.append("")

    if audit.report.imbalance_notes:
        body.append("## Imbalance Notes")
        body.append("")
        for note in audit.report.imbalance_notes:
            body.append(f"- {note}")
        body.append("")

    if audit.objections:
        body.append("## Exam-Level Findings (report-only)")
        body.append("")
        body.append("These exam-wide concerns surfaced during the audit. They are "
                    "reported for instructor review; the pipeline does not "
                    "auto-remediate them.")
        body.append("")
        body.append("| Severity | Category | Claim | Suggested Fix |")
        body.append("|:---------|:---------|:------|:--------------|")
        for o in audit.objections:
            fix = (o.suggested_fix or "—").replace("|", "\\|")
            claim = o.claim.replace("|", "\\|")
            body.append(f"| {o.severity.value} | {o.category} | {claim} | {fix} |")
        body.append("")

    # Per-item provenance summary at the end — short, defensible record.
    survivors = _surviving_items(draft)
    body.append("## Item Provenance (summary)")
    body.append("")
    body.append("| Item | Points | Bloom | Type | Provenance events |")
    body.append("|:-----|------:|:------|:-----|------------------:|")
    for it in survivors:
        body.append(f"| {it.id} | {it.points} | {it.bloom_level.value} | "
                    f"{it.type.value} | {len(it.provenance)} |")
    body.append("")

    return "\n".join(body)


# ---- variants ------------------------------------------------------------

def build_variant_qmd(variant: ItemVariant, base_item: Item, exam_spec: ExamSpec) -> str:
    fm = _FRONTMATTER_ALL_FORMATS.format(
        title=f"Variant — {variant.kind.value.replace('_', ' ')}",
        subtitle=f"Adapted from item {variant.base_item_id}.",
        date=_today(),
    )
    v = variant.item
    chunks: list[str] = [
        fm,
        f"## Question (adapted from {variant.base_item_id})",
        "",
        f"*Type: {v.type.value.replace('_', ' ')} · Bloom: {v.bloom_level.value} · "
        f"Difficulty: {v.difficulty_est.value} · Points: {v.points}*",
        "",
        _fix_bullet_spacing(v.stem),
        "",
    ]
    if v.type == ItemType.MCQ and v.options:
        letters = "ABCDEFGH"
        for i, opt in enumerate(v.options):
            chunks.append(f"{letters[i]}. {opt}")
        chunks.append("")
    chunks.append("---")
    chunks.append("")
    chunks.append("## Adaptation notes")
    chunks.append("")
    chunks.append(variant.adaptation_notes)
    chunks.append("")
    return "\n".join(chunks)


# ---- helpers -------------------------------------------------------------

def _surviving_items(draft: ExamDraft) -> list[Item]:
    return [it for it in draft.items if it.status != ItemStatus.REJECTED]

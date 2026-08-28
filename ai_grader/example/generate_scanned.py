#!/usr/bin/env python3
"""
Generate simulated, hand-filled student responses to w09c01_quiz.qmd and
append them into a single image-based (raster) PDF: scanned.pdf.

Pipeline per student:
  HTML (script font for name + answers)  -->  Chrome headless print-to-PDF
  -->  pdftoppm rasterize @150dpi  -->  PIL scan effect (grayscale/rotate/noise)
  -->  concatenate all page images into scanned.pdf (image-only => needs OCR).
"""

import csv
import html
import os
import random
import subprocess
import sys
import tempfile

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "students.csv")
OUT_PDF = os.path.join(HERE, "scanned.pdf")
DATE = "Mar 12, 2026"
DPI = 150
SCRIPT_FONT = "Z003"          # URW Chancery — a calligraphic script face
PRINT_FONT = "Tinos, 'Liberation Serif', serif"

# ---------------------------------------------------------------------------
# The quiz questions (verbatim prompt text, trimmed for the worksheet layout).
# ---------------------------------------------------------------------------
QUESTIONS = [
    ("1.", "Module 9 catalogs the centralized patterns — Orchestrator-Worker, "
           "Hierarchical, Supervisor-Router, Sequential Pipeline. Take a task that is a "
           "genuinely <i>fixed</i> sequence (extract &rarr; transform &rarr; summarize &rarr; cite) "
           "and one where the next sub-task <i>depends unpredictably</i> on prior results, and say "
           "which centralized pattern fits each — then explain why centralized control trades "
           "flexibility for predictability and debuggability."),
    ("2.", "Module 9 warns about the <b>coordination tax</b> — every added agent multiplies "
           "delegation calls, latency, failure modes, and debugging difficulty. You are tempted to "
           "add a fourth worker to an orchestrator-worker system. Explain what specific benefit "
           "would have to materialize for that fourth agent to be worth the tax, and name a failure "
           "mode (e.g., underspecified sub-tasks, lossy aggregation, cascading failure) you would be "
           "inviting by adding it."),
    ("3.", "Primers 19 and 20 present MCP and FastMCP as the standardized way agents get tools. In a "
           "centralized multi-agent system where several workers each need overlapping capabilities, "
           "explain why exposing those capabilities through a <i>shared MCP tool protocol</i> "
           "(rather than hand-wiring tools into each agent) matters — and what a FastMCP server buys "
           "you when you want to swap or version a tool without rewriting every worker."),
]

# ---------------------------------------------------------------------------
# Per-student answers, keyed by student_id. Written to model variable accuracy
# at a "basic proficiency" baseline: a few strong, a cluster of average, a few weak.
# ---------------------------------------------------------------------------
ANSWERS = {
    # Emily Nguyen — strong (~90%)
    "906214537": [
        "The extract -> transform -> summarize -> cite task is a Sequential Pipeline because the "
        "order is fixed in code and no agent decides what comes next at runtime. The one where the "
        "next step depends on prior results is a Supervisor-Router: it looks at the current state "
        "after each turn and routes to whichever agent should go next. Centralized control trades "
        "flexibility for predictability because one controller owns the ordering, so there is a "
        "single place you can inspect when something breaks.",
        "A fourth worker only pays for the coordination tax if it buys real parallelism, or handles a "
        "job that needs a conflicting prompt the other three can't share -- you earn the agent by "
        "naming how three workers actually failed, not by wanting more. The failure mode I'd be "
        "inviting is bad decomposition: the orchestrator now has to re-split the task into four "
        "pieces that can overlap or leave gaps, and aggregation can hide a worker that quietly "
        "returned a wrong answer.",
        "Exposing capabilities through a shared MCP protocol means each tool is defined once behind a "
        "standard interface instead of being hand-wired and separately maintained inside every "
        "worker, so a fix lands everywhere at once. A FastMCP server lets you swap or version the "
        "implementation behind that same interface, so none of the workers need rewriting when a "
        "tool changes. That keeps the tools as shared infrastructure, separate from the controller "
        "that owns the flow.",
    ],
    # Rohan Patel — strong (~85%)
    "906331082": [
        "Fixed sequence = Sequential Pipeline, since you wire the order in code. Unpredictable next "
        "step = Supervisor-Router, because the supervisor re-decides each turn based on the latest "
        "state, kind of like ReAct at the system level. Centralized control is more predictable and "
        "debuggable because a single named component decided the order, so there is one place to "
        "look.",
        "The fourth agent is worth it only if something specific materializes -- like independent "
        "verification, or domain-specific tooling the others shouldn't carry -- otherwise you're just "
        "paying more latency and delegation calls for nothing. The failure mode I invite is lossy "
        "aggregation: with four outputs to merge, the orchestrator can drop or blur part of a "
        "worker's result.",
        "A shared MCP tool protocol means the capability is written once and every worker calls it the "
        "same way, instead of duplicating drifting tool code in each agent. FastMCP gives you a clean "
        "seam -- workers depend on the interface, so you can version the tool without touching the "
        "workers.",
    ],
    # Marcus Johnson — average (~65%)
    "905998741": [
        "The extract-transform-summarize-cite one is a Sequential Pipeline because it always runs in "
        "the same order. The other one is a Supervisor-Router since you don't know the next step "
        "ahead of time. Centralized control is easier to debug because everything goes through one "
        "controller.",
        "You'd add the fourth worker if it makes the system faster by running things in parallel. The "
        "failure mode is that it might not work right and give a wrong answer.",
        "Using a shared MCP protocol is better because you don't have to wire the tool into every "
        "agent separately, you just define it once. FastMCP lets you update the tool without breaking "
        "the workers.",
    ],
    # Sofia Garcia — average-good (~72%)
    "906187203": [
        "The fixed extract -> transform -> summarize -> cite chain is a Sequential Pipeline; the order "
        "is set in the code and nothing chooses it at runtime. The task where the next sub-task "
        "depends on prior results is a Supervisor-Router, because the supervisor inspects the state "
        "and routes the next turn. Centralized control gives up flexibility but you get "
        "predictability since one controller owns the flow and you can trace its decisions.",
        "A fourth worker is only worth the coordination tax if it adds genuine parallelism or a "
        "capability the other three can't cover. The failure mode is cascading failure -- if the new "
        "worker breaks, its bad output can flow downstream into the aggregation.",
        "Shared MCP tools matter because several workers need the same capabilities, and defining them "
        "once behind a protocol avoids maintaining copies in each agent. FastMCP helps you swap a "
        "tool version out when you need to.",
    ],
    # Daniel Kim — below average (~50%), confuses supervisor with orchestrator
    "906402915": [
        "The first task is a Sequential Pipeline. The second one, where the next step depends on the "
        "results, is an Orchestrator-Worker because the orchestrator decides what to do next. "
        "Centralized control is more predictable because there's one controller running it.",
        "Adding a fourth worker would be worth it if it can do more work and help the system. A "
        "failure mode is that adding agents makes it more complex.",
        "A shared MCP protocol is a standard way for agents to get tools so they all use the same "
        "setup. FastMCP is a server that hosts the tools so you can change them.",
    ],
    # Katie O'Brien — average-good (~72%)
    "905874360": [
        "Extract -> transform -> summarize -> cite is a Sequential Pipeline because it's a fixed chain "
        "wired in code. The unpredictable one is a Supervisor-Router, which routes each turn from the "
        "current state. Centralized control trades flexibility for predictability and debuggability "
        "because a single controller owns the ordering -- one place decided it, so one place to "
        "inspect when it misbehaves.",
        "The fourth worker only justifies the tax if it delivers something concrete, like parallelism "
        "from an independent sub-task that now runs at the same time. By adding it I'm inviting "
        "over-decomposition -- splitting the task into more pieces than it needs, which can overlap "
        "or miss the goal.",
        "Going through a shared MCP protocol means you define the capability once and every worker "
        "calls it the same way instead of hand-wiring it into each one. FastMCP lets you version a "
        "tool behind the interface so workers keep calling it the same way.",
    ],
    # Jamal Williams — average (~60%), thin trade explanation
    "906259188": [
        "The fixed sequence is a Sequential Pipeline. The one that depends on prior results is a "
        "Supervisor-Router. Centralized means one controller runs the show, which makes it "
        "predictable.",
        "You'd only add the fourth worker if it runs a separate task in parallel and actually speeds "
        "things up. The failure I'd invite is underspecified sub-tasks -- the orchestrator might hand "
        "it a fuzzy job it can't do well.",
        "It's better to use a shared MCP protocol so you're not rebuilding the same tool inside every "
        "worker. FastMCP means you can swap the tool implementation and the workers still call it the "
        "same way through the interface.",
    ],
    # Elena Rossi — strong-ish (~80%)
    "906310477": [
        "The extract -> transform -> summarize -> cite job is a Sequential Pipeline -- the order is "
        "fixed in code with no routing at runtime. The task whose next step depends unpredictably on "
        "prior output is a Supervisor-Router, which re-decides the next agent each turn from the "
        "state. Centralized control trades flexibility for predictability because one named component "
        "owns the control flow, so there is one place that decided the order and one place to debug.",
        "A fourth worker only earns its keep if a specific benefit shows up, like independent "
        "verification of the other workers' output -- you don't add it just because you want to. The "
        "failure mode I invite is lossy aggregation, where merging four results drops or distorts "
        "part of what a worker produced.",
        "A shared MCP protocol defines each capability once behind a standard interface so the workers "
        "don't each carry their own drifting copy. FastMCP buys a clean seam for change when you swap "
        "a tool.",
    ],
    # Grace Thompson — below average (~45%), definitions only / vague
    "905963028": [
        "The first one is a Sequential Pipeline because the steps are in order. The second is a "
        "Supervisor-Router.",
        "A fourth agent would be worth it if it helps the system do the task better. A failure mode is "
        "that it could be wrong or slow.",
        "MCP is a standardized way for agents to use tools. FastMCP is a framework for building MCP "
        "servers so you can make tools easily.",
    ],
    # Yusuf Ahmed — weak (~30%), wrong patterns
    "906428650": [
        "I think the fixed one is an Orchestrator-Worker and the changing one is a Hierarchical "
        "pattern because the boss agent manages the others. Centralized just means it's all "
        "controlled in one place.",
        "Adding another worker is good because more agents can get more done, so the fourth one would "
        "help finish faster. A downside is it costs more.",
        "MCP lets agents share tools so you don't have to build them again. FastMCP is faster.",
    ],
}


def build_html(student, answers, jitter):
    """Return a full HTML worksheet for one student."""
    name = f"{student['first_name']} {student['last_name']}"
    ink = jitter["ink"]
    fs = jitter["font_size"]
    ls = jitter["letter_spacing"]
    lh = jitter["line_height"]
    slant = jitter["slant"]

    q_blocks = []
    for (num, qtext), ans in zip(QUESTIONS, answers):
        ans_html = html.escape(ans)
        q_blocks.append(f"""
        <div class="q">
          <p class="qtext"><b>{num}</b> {qtext}</p>
          <div class="answerbox">
            <div class="handwritten">{ans_html}</div>
          </div>
        </div>""")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  @page {{ size: Letter; margin: 0.7in 0.8in; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: {PRINT_FONT}; color: #1a1a1a; font-size: 11.5pt; line-height: 1.35; }}
  h1 {{ font-size: 17pt; margin: 0 0 2px 0; }}
  .sub {{ font-size: 12.5pt; color: #333; font-weight: bold; margin: 0 0 14px 0; }}
  .namerow {{ font-size: 12pt; margin: 6px 0 4px 0; }}
  .filled {{
    font-family: '{SCRIPT_FONT}', cursive;
    color: {ink};
    font-size: {fs + 2}pt;
    transform: rotate({slant}deg);
    display: inline-block;
    padding: 0 6px;
  }}
  .note {{ border-left: 3px solid #4a6; background:#f4f8f4; padding:7px 11px;
           font-size:10.5pt; margin: 8px 0 16px 0; color:#245; }}
  .q {{ margin: 0 0 15px 0; page-break-inside: avoid; }}
  .qtext {{ margin: 0 0 6px 0; text-align: justify; }}
  .answerbox {{
    min-height: 8.2em;
    border: 1px solid #cfcfcf;
    border-radius: 2px;
    padding: 8px 12px;
    background:
      repeating-linear-gradient(#ffffff 0px, #ffffff 27px, #e9edf2 27px, #e9edf2 28px);
  }}
  .handwritten {{
    font-family: '{SCRIPT_FONT}', cursive;
    color: {ink};
    font-size: {fs}pt;
    line-height: {lh};
    letter-spacing: {ls}px;
    transform: rotate({slant}deg);
    transform-origin: left top;
  }}
</style></head>
<body>
  <h1>Quiz — Week 9, Class 1</h1>
  <div class="sub">Centralized Multi-Agent Patterns</div>
  <div class="namerow"><b>Name:</b>
     <span class="filled">{html.escape(name)}</span>
     &nbsp;&nbsp;&nbsp;<b>Date:</b>
     <span class="filled">{html.escape(DATE)}</span></div>
  <div class="note">Short answer — <b>2–4 sentences each.</b> Explain your reasoning and how the
     idea applies to agentic design; definitions alone will not score.</div>
  {''.join(q_blocks)}
</body></html>"""


def find_chrome():
    for exe in ("google-chrome", "chromium", "chromium-browser"):
        if subprocess.run(["which", exe], capture_output=True).returncode == 0:
            return exe
    sys.exit("No Chrome/Chromium found.")


def render_pdf(chrome, html_path, pdf_path):
    subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", f"file://{html_path}"],
        check=True, capture_output=True,
    )


def scan_effect(img, seed):
    """Make a clean render look like a photocopied scan: grayscale, faint
    rotation, subtle blur + speckle noise. Forces OCR on the final PDF."""
    rnd = random.Random(seed)
    img = img.convert("L")
    angle = rnd.uniform(-0.8, 0.8)
    img = img.rotate(angle, expand=False, fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.4))
    # speckle: sparse darker pixels
    px = img.load()
    w, h = img.size
    for _ in range(int(w * h * 0.0008)):
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = max(0, px[x, y] - rnd.randrange(40, 120))
    return img.convert("RGB")


def main():
    chrome = find_chrome()
    with open(CSV_PATH, newline="") as f:
        students = list(csv.DictReader(f))

    pages = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(students):
            sid = s["student_id"]
            rnd = random.Random(int(sid))
            jitter = {
                "ink": rnd.choice(["#111318", "#14213d", "#1b1b2f", "#0d1b2a", "#20263b"]),
                "font_size": rnd.choice([13.5, 14, 14.5, 15]),
                "letter_spacing": round(rnd.uniform(0.0, 0.6), 2),
                "line_height": round(rnd.uniform(1.5, 1.75), 2),
                "slant": round(rnd.uniform(-0.6, 0.6), 2),
            }
            answers = ANSWERS[sid]
            html_doc = build_html(s, answers, jitter)
            html_path = os.path.join(tmp, f"s{i}.html")
            pdf_path = os.path.join(tmp, f"s{i}.pdf")
            with open(html_path, "w") as fh:
                fh.write(html_doc)
            render_pdf(chrome, html_path, pdf_path)

            # rasterize each page of this student's PDF
            prefix = os.path.join(tmp, f"s{i}_pg")
            subprocess.run(["pdftoppm", "-r", str(DPI), "-png", pdf_path, prefix],
                           check=True, capture_output=True)
            page_files = sorted(p for p in os.listdir(tmp)
                                if p.startswith(f"s{i}_pg") and p.endswith(".png"))
            for pf in page_files:
                img = Image.open(os.path.join(tmp, pf))
                img = scan_effect(img, seed=int(sid) + len(pages))
                pages.append(img)
            print(f"  rendered {s['first_name']} {s['last_name']} "
                  f"({len(page_files)} page{'s' if len(page_files) != 1 else ''})")

        pages[0].save(OUT_PDF, "PDF", resolution=DPI, save_all=True,
                      append_images=pages[1:])
    print(f"\nWrote {OUT_PDF}  ({len(pages)} pages, image-based / OCR-required)")


if __name__ == "__main__":
    main()

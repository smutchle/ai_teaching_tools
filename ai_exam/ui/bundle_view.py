"""Phase-4 export bundle browser.

Lists every file produced under `exam_bundle/` for a selected run and offers
download buttons. PDFs render inline via `st.pdf_viewer`-style embed when
small enough; large files are download-only to keep the page responsive.
A single 'Download all as .zip' button at the top covers the whole bundle.
"""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import streamlit as st

from ui.event_loader import list_runs, run_summary


_INLINE_PDF_MAX_BYTES = 5 * 1024 * 1024  # 5 MB — bigger than this, just offer download.


def _file_row(path: Path) -> None:
    name = path.name
    size = path.stat().st_size
    col_name, col_size, col_btn = st.columns([5, 1, 1])
    col_name.markdown(f"`{name}`")
    col_size.markdown(f"{size:,} B")
    col_btn.download_button(
        "Download", data=path.read_bytes(), file_name=name, key=f"dl::{path}",
        use_container_width=True,
    )


def _pdf_inline(path: Path) -> None:
    if path.stat().st_size > _INLINE_PDF_MAX_BYTES:
        st.caption(
            f"`{path.name}` is {path.stat().st_size / 1024 / 1024:.1f} MB — "
            f"download to view."
        )
        return
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    st.markdown(
        f"<iframe src='data:application/pdf;base64,{b64}' "
        f"width='100%' height='800px' style='border: 1px solid #ddd;'></iframe>",
        unsafe_allow_html=True,
    )


def render_bundle_page(*, runs_dir: Path) -> None:
    st.title("Export Bundle")

    runs = list_runs(runs_dir)
    runs_with_bundle = [r for r in runs if (r / "exam_bundle").exists()]
    if not runs_with_bundle:
        st.info(
            "No runs have produced an `exam_bundle/` yet. Run "
            "`python run.py --pdf <yours.pdf>` through Phase 4 to populate."
        )
        return

    with st.sidebar:
        st.header("Run")
        picked_idx = st.selectbox(
            "Select run", range(len(runs_with_bundle)),
            format_func=lambda i: runs_with_bundle[i].name, index=0,
        )
        picked = runs_with_bundle[picked_idx]

    bundle = picked / "exam_bundle"
    primary = sorted(p for p in bundle.iterdir() if p.is_file())
    variants_dir = bundle / "variants"
    variants = sorted(variants_dir.iterdir()) if variants_dir.exists() else []

    # ---- whole-bundle download ----
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in bundle.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=str(f.relative_to(bundle.parent)))
    n_files = sum(1 for f in bundle.rglob("*") if f.is_file())
    zip_size = zip_buf.tell()
    st.download_button(
        f"📥 Download entire bundle as .zip  ({n_files} files, {zip_size:,} bytes)",
        data=zip_buf.getvalue(),
        file_name=f"{picked.name}_exam_bundle.zip",
        mime="application/zip",
        type="primary",
        use_container_width=True,
        key="zip_all",
    )
    st.divider()

    # Group primary files by stem so each artifact gets one section with its
    # rendered formats together (e.g., exam.qmd / exam.pdf / exam.docx / exam.tex).
    by_stem: dict[str, list[Path]] = {}
    for p in primary:
        by_stem.setdefault(p.stem, []).append(p)

    artifact_order = ["exam", "answer_key", "instructor_notes", "rubrics",
                      "exam_report", "provenance", "render_failures"]
    ordered_stems = [s for s in artifact_order if s in by_stem] + [
        s for s in by_stem if s not in artifact_order
    ]

    for stem in ordered_stems:
        files = sorted(by_stem[stem], key=lambda p: p.suffix)
        with st.expander(f"**{stem}**  ({len(files)} files)", expanded=(stem == "exam")):
            for f in files:
                _file_row(f)
            # Inline-preview the PDF if present.
            pdf = next((f for f in files if f.suffix == ".pdf"), None)
            if pdf is not None:
                st.markdown("**Inline preview:**")
                _pdf_inline(pdf)

    if variants:
        st.subheader("Variants")
        for v in variants:
            _file_row(v)

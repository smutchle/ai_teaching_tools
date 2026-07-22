"""Usage Metrics dashboard page for the Notes Converter.

Shows bar charts of conversion activity filterable by Year, Year/Month,
and Department, plus a CSV download of the (filtered) raw metrics.
"""

from __future__ import annotations

import calendar
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Make sibling modules importable regardless of how Streamlit resolves paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usage_metrics import CSV_FIELDS, load_usage_data
from vt_banner import VT_MAROON, render_vt_banner

ALL_YEARS: str = "All years"
ALL_MONTHS: str = "All months"

MONTH_NAMES: list[str] = list(calendar.month_name)[1:]  # "January".."December"
MONTH_ABBRS: list[str] = list(calendar.month_abbr)[1:]  # "Jan".."Dec"


def time_bar_chart(
    data: pd.DataFrame,
    x_title: str,
    y_field: str,
    y_title: str,
    x_sort: list[str] | None,
) -> alt.Chart:
    """Vertical single-hue bar chart of a usage measure over time buckets.

    Args:
        data: Aggregated frame with a 'bucket' column plus the measure column.
        x_title: Axis title for the time bucket.
        y_field: Measure column name ('num_pdfs' or 'total_pages').
        y_title: Axis title for the measure.
        x_sort: Explicit bucket order (None = natural/lexicographic order).

    Returns:
        Configured Altair chart.
    """
    return (
        alt.Chart(data)
        .mark_bar(color=VT_MAROON, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("bucket:O", title=x_title, sort=x_sort),
            y=alt.Y(f"{y_field}:Q", title=y_title),
            tooltip=[
                alt.Tooltip("bucket:O", title=x_title),
                alt.Tooltip(f"{y_field}:Q", title=y_title, format=","),
            ],
        )
        .properties(height=320)
    )


def department_bar_chart(data: pd.DataFrame) -> alt.Chart:
    """Horizontal single-hue bar chart of usage by department, sorted descending.

    Args:
        data: Aggregated frame with department, num_pdfs, and total_pages columns.

    Returns:
        Configured Altair chart.
    """
    height = max(160, 30 * len(data))
    return (
        alt.Chart(data)
        .mark_bar(color=VT_MAROON, cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            y=alt.Y("department:N", sort="-x", title=None),
            x=alt.X("num_pdfs:Q", title="PDFs converted"),
            tooltip=[
                alt.Tooltip("department:N", title="Department"),
                alt.Tooltip("num_pdfs:Q", title="PDFs converted", format=","),
                alt.Tooltip("total_pages:Q", title="Pages converted", format=","),
            ],
        )
        .properties(height=height)
    )


def main() -> None:
    st.set_page_config(
        page_title="Usage Metrics - Notes Converter",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    render_vt_banner()
    st.title("📊 Usage Metrics")
    st.markdown(
        "Conversion activity for the AI Handwritten Notes Converter. "
        "Filter by year, month, and inferred Virginia Tech department, "
        "then download the data as CSV."
    )

    df = load_usage_data()
    if df.empty:
        st.info(
            "No usage metrics recorded yet. Metrics are captured automatically "
            "each time a PDF is converted on the main page."
        )
        return

    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month

    # --- Filters (one row above the charts) ---
    filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 2])
    with filter_col1:
        years = sorted(df["year"].unique().tolist(), reverse=True)
        year_choice = st.selectbox("Year", [ALL_YEARS] + [str(y) for y in years])
    with filter_col2:
        month_choice = st.selectbox("Month", [ALL_MONTHS] + MONTH_NAMES)
    with filter_col3:
        all_departments = sorted(df["department"].unique().tolist())
        department_choice = st.multiselect(
            "Departments (leave empty for all)", all_departments
        )

    filtered = df
    if year_choice != ALL_YEARS:
        filtered = filtered[filtered["year"] == int(year_choice)]
    if month_choice != ALL_MONTHS:
        filtered = filtered[filtered["month"] == MONTH_NAMES.index(month_choice) + 1]
    if department_choice:
        filtered = filtered[filtered["department"].isin(department_choice)]

    if filtered.empty:
        st.warning("No conversions match the selected filters.")
        return

    # --- Summary tiles ---
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Conversions", f"{len(filtered):,}")
    kpi2.metric("PDFs converted", f"{int(filtered['num_pdfs'].sum()):,}")
    kpi3.metric("Pages converted", f"{int(filtered['total_pages'].sum()):,}")
    kpi4.metric("Departments", f"{filtered['department'].nunique():,}")

    # --- Time bucketing: year -> month -> day depending on filter depth ---
    if year_choice != ALL_YEARS and month_choice != ALL_MONTHS:
        bucketed = filtered.assign(bucket=filtered["timestamp"].dt.strftime("%b %d"))
        bucket_title = f"Day ({month_choice} {year_choice})"
        bucket_sort: list[str] | None = (
            bucketed.sort_values("timestamp")["bucket"].unique().tolist()
        )
    elif year_choice != ALL_YEARS:
        bucketed = filtered.assign(
            bucket=filtered["month"].map(lambda m: MONTH_ABBRS[m - 1])
        )
        bucket_title = f"Month ({year_choice})"
        bucket_sort = MONTH_ABBRS
    else:
        bucketed = filtered.assign(bucket=filtered["year"].astype(str))
        bucket_title = "Year"
        bucket_sort = None

    time_agg = (
        bucketed.groupby("bucket", as_index=False)[["num_pdfs", "total_pages"]]
        .sum()
    )

    st.markdown("### Usage Over Time")
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.altair_chart(
            time_bar_chart(time_agg, bucket_title, "num_pdfs", "PDFs converted", bucket_sort),
            use_container_width=True,
        )
    with chart_col2:
        st.altair_chart(
            time_bar_chart(time_agg, bucket_title, "total_pages", "Pages converted", bucket_sort),
            use_container_width=True,
        )

    st.markdown("### Usage by Department")
    dept_agg = (
        filtered.groupby("department", as_index=False)[["num_pdfs", "total_pages"]]
        .sum()
        .sort_values("num_pdfs", ascending=False)
    )
    st.altair_chart(department_bar_chart(dept_agg), use_container_width=True)

    # --- Raw data + CSV downloads ---
    st.markdown("### Data")
    with st.expander("📋 View raw records", expanded=False):
        st.dataframe(
            filtered[CSV_FIELDS].sort_values("timestamp", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

    download_col1, download_col2 = st.columns(2)
    with download_col1:
        st.download_button(
            label="⬇️ Download filtered data (CSV)",
            data=filtered[CSV_FIELDS].to_csv(index=False),
            file_name="usage_metrics_filtered.csv",
            mime="text/csv",
            help="Download the records matching the current filters",
        )
    with download_col2:
        st.download_button(
            label="⬇️ Download all data (CSV)",
            data=df[CSV_FIELDS].to_csv(index=False),
            file_name="usage_metrics.csv",
            mime="text/csv",
            help="Download every recorded conversion, ignoring filters",
        )


main()

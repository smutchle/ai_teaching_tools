"""Usage-metrics capture and retrieval for the Notes Converter.

One row is appended to a local CSV per completed conversion:
timestamp, number of PDFs converted, total pages converted, and the
academic department inferred by the LLM from the extracted content.
The CSV doubles as the raw download offered on the Usage Metrics page.
"""

from __future__ import annotations

import csv
import fcntl
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
from anthropic import Anthropic
from anthropic.types import TextBlock

from vt_departments import UNKNOWN_DEPARTMENT, VT_DEPARTMENTS

METRICS_CSV_PATH: Path = Path(__file__).parent / "usage_metrics.csv"

CSV_FIELDS: list[str] = ["timestamp", "num_pdfs", "total_pages", "department"]


def first_text_block(message) -> TextBlock:
    """Return the first TextBlock in an API response's content.

    Models with extended thinking enabled prepend a ThinkingBlock (and may
    interleave other block types), so the text is not guaranteed to be at
    index 0. This scans for the first genuine TextBlock instead of assuming
    its position.

    Args:
        message: The anthropic.types.Message returned by messages.create.

    Returns:
        The first TextBlock found in message.content.

    Raises:
        TypeError: If the response contains no TextBlock.
    """
    for block in message.content:
        if isinstance(block, TextBlock):
            return block
    block_types = [type(b).__name__ for b in message.content]
    raise TypeError(f"Expected a TextBlock in response but got {block_types}")


@dataclass(frozen=True)
class UsageRecord:
    """A single conversion event captured for usage reporting."""

    timestamp: datetime
    num_pdfs: int
    total_pages: int
    department: str


def infer_department(client: Anthropic, model: str, notes_sample: str) -> str:
    """Ask the LLM which Virginia Tech department the notes most likely belong to.

    Args:
        client: Configured Anthropic client.
        model: Model identifier to use for the classification call.
        notes_sample: Representative extracted text from the converted notes.

    Returns:
        A department name from VT_DEPARTMENTS. Returns UNKNOWN_DEPARTMENT when
        the model's answer does not match any known department.

    Raises:
        anthropic.APIError: If the classification API call fails.
        TypeError: If the API returns a non-text content block.
    """
    department_list = "\n".join(f"- {d}" for d in VT_DEPARTMENTS)
    prompt = f"""You are classifying a set of converted academic notes by the Virginia Tech department they most likely belong to.

Here is a sample of the notes content:

=== NOTES SAMPLE ===
{notes_sample}
=== END SAMPLE ===

Choose the single most likely department from this exact list:

{department_list}

Respond with ONLY the department name, copied exactly from the list above. If the content does not clearly fit any department, respond with "{UNKNOWN_DEPARTMENT}"."""

    message = client.messages.create(
        model=model,
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}],
    )

    answer = first_text_block(message).text.strip().strip('"').strip("'")
    for department in VT_DEPARTMENTS:
        if department.lower() == answer.lower():
            return department
    return UNKNOWN_DEPARTMENT


def record_conversion(record: UsageRecord, csv_path: Path = METRICS_CSV_PATH) -> None:
    """Append one conversion record to the metrics CSV, creating it if needed.

    An exclusive advisory lock is held while writing so concurrent Streamlit
    sessions cannot interleave rows.

    Args:
        record: The conversion event to persist.
        csv_path: Destination CSV file.

    Raises:
        OSError: If the file cannot be opened, locked, or written.
    """
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(CSV_FIELDS)
            writer.writerow(
                [
                    record.timestamp.isoformat(timespec="seconds"),
                    record.num_pdfs,
                    record.total_pages,
                    record.department,
                ]
            )
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def load_usage_data(csv_path: Path = METRICS_CSV_PATH) -> pd.DataFrame:
    """Load all recorded usage metrics.

    Args:
        csv_path: Source CSV file.

    Returns:
        DataFrame with columns timestamp (datetime64), num_pdfs (int),
        total_pages (int), department (str). Empty (with the same columns)
        when no metrics have been recorded yet.
    """
    if not csv_path.exists():
        return pd.DataFrame(
            {
                "timestamp": pd.Series(dtype="datetime64[ns]"),
                "num_pdfs": pd.Series(dtype="int64"),
                "total_pages": pd.Series(dtype="int64"),
                "department": pd.Series(dtype="str"),
            }
        )
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df

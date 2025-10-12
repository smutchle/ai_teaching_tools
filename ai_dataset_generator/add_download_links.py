#!/usr/bin/env python3
"""
Add prominent download links to all .qmd dataset description files.

This script adds a download section with buttons for both clean and dirty CSV versions
at the top of each dataset description, right after the Overview heading.
"""

import re
from pathlib import Path


def extract_dataset_name_from_qmd(qmd_path: Path) -> str:
    """
    Extract the dataset base name from the qmd file by reading the CSV path in the file.

    Args:
        qmd_path: Path to the .qmd file

    Returns:
        Dataset name (e.g., 'ds001_business_customer_lifetime_value')
    """
    with open(qmd_path, 'r') as f:
        content = f.read()

    # Look for CSV paths in the file
    match = re.search(r'`csv/(ds\d+_[^.]+)\.csv`', content)
    if match:
        return match.group(1)

    # Fallback: derive from filename
    return qmd_path.stem


def create_download_section(dataset_name: str) -> str:
    """
    Create the download section HTML/Markdown.

    Args:
        dataset_name: Base name of the dataset

    Returns:
        Formatted download section
    """
    clean_csv = f"../csv/{dataset_name}.csv"
    dirty_csv = f"../csv/{dataset_name}_dirty.csv"

    return f'''
::: {{.callout-note icon=false}}
## Download Dataset

<div style="display: flex; gap: 10px; margin-top: 10px;">
  <a href="{clean_csv}" download class="btn btn-primary" style="text-decoration: none; padding: 10px 20px; background-color: #0d6efd; color: white; border-radius: 5px; font-weight: bold;">
    📥 Download Clean Version
  </a>
  <a href="{dirty_csv}" download class="btn btn-secondary" style="text-decoration: none; padding: 10px 20px; background-color: #6c757d; color: white; border-radius: 5px; font-weight: bold;">
    📥 Download Dirty Version
  </a>
</div>

**Clean Version**: Complete data with no missing values or outliers
**Dirty Version**: Contains missing values and outliers for data cleaning practice
:::

'''


def add_download_links_to_qmd(qmd_path: Path) -> bool:
    """
    Add download links to a .qmd file if they don't already exist.

    Args:
        qmd_path: Path to the .qmd file

    Returns:
        True if file was modified, False if already had download links
    """
    with open(qmd_path, 'r') as f:
        content = f.read()

    # Check if download section already exists
    if 'Download Dataset' in content or 'Download Clean Version' in content:
        return False

    # Extract dataset name
    dataset_name = extract_dataset_name_from_qmd(qmd_path)

    # Create download section
    download_section = create_download_section(dataset_name)

    # Find the position after "# Overview" heading and its content
    # We want to insert after the overview paragraph but before "# Background"
    pattern = r'(# Overview\s*\n\n.*?\n)(\n# )'

    match = re.search(pattern, content, re.DOTALL)
    if match:
        # Insert download section after overview, before next section
        new_content = content[:match.end(1)] + download_section + content[match.end(1):]
    else:
        # Fallback: insert after the YAML front matter and title
        pattern_fallback = r'(---\n.*?---\n\n)(# Overview)'
        match_fallback = re.search(pattern_fallback, content, re.DOTALL)
        if match_fallback:
            new_content = content[:match_fallback.end(1)] + download_section + '\n' + content[match_fallback.end(1):]
        else:
            print(f"  Warning: Could not find insertion point for {qmd_path.name}")
            return False

    # Write back to file
    with open(qmd_path, 'w') as f:
        f.write(new_content)

    return True


def main():
    """Main execution function."""
    print("=" * 80)
    print("Adding Download Links to Dataset Description Files")
    print("=" * 80)

    # Find all .qmd files
    dataset_dir = Path("./ads_datasets/dataset_descriptions")

    if not dataset_dir.exists():
        print(f"Error: Directory not found: {dataset_dir}")
        return

    qmd_files = sorted(dataset_dir.glob("ds*.qmd"))

    if not qmd_files:
        print(f"Error: No .qmd files found in {dataset_dir}")
        return

    print(f"\nFound {len(qmd_files)} dataset description files")
    print(f"Processing...\n")

    modified_count = 0
    skipped_count = 0

    for qmd_path in qmd_files:
        if add_download_links_to_qmd(qmd_path):
            print(f"✓ Modified: {qmd_path.name}")
            modified_count += 1
        else:
            print(f"⏭ Skipped: {qmd_path.name} (already has download links)")
            skipped_count += 1

    print(f"\n{'=' * 80}")
    print("Processing Complete!")
    print(f"{'=' * 80}")
    print(f"Modified: {modified_count} files")
    print(f"Skipped: {skipped_count} files")
    print(f"Total: {len(qmd_files)} files")


if __name__ == "__main__":
    main()

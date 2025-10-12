#!/usr/bin/env python3
"""
Generate dataset definitions and Quarto documentation for 100 datasets.

This script:
1. Reads dataset configurations from combined_datasets_json.json
2. Uses AnthropicChatBot to generate detailed dataset definitions using LLM
3. Creates clean and dirty versions for each dataset
4. Generates Quarto markdown documentation for each dataset
5. Creates an index.qmd and _quarto.yml for a Quarto book
"""

import json
import os
import re
import shutil
from pathlib import Path
from dotenv import load_dotenv
from AnthropicChatBot import AnthropicChatBot
from dataset import Dataset
from dataset_generator import DatasetGenerator


# Configuration
OUTPUT_DIR = Path("./ads_datasets")
CSV_DIR = OUTPUT_DIR / "csv"
DESCRIPTIONS_DIR = OUTPUT_DIR / "dataset_descriptions"
DEFINITIONS_DIR = OUTPUT_DIR / "definitions"

# Dataset parameters
MISSING_RATE = 0.02  # 2% missing values for dirty datasets
OUTLIER_RATE = 0.01  # 1% outliers for dirty datasets
N_ROWS = 500000  # Number of rows per dataset


def setup_directories():
    """Create necessary directories."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    CSV_DIR.mkdir(exist_ok=True)
    DESCRIPTIONS_DIR.mkdir(exist_ok=True)
    DEFINITIONS_DIR.mkdir(exist_ok=True)


def clean_name(text):
    """Convert text to snake_case identifier."""
    # Remove special characters and replace spaces with underscores
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '_', text)
    return text.lower()


def create_dataset_name(dataset_id, target, domain):
    """Create a unique dataset name."""
    clean_target = clean_name(target)
    clean_domain = clean_name(domain)
    return f"ds{dataset_id:03d}_{clean_domain}_{clean_target}"


def load_api_documentation():
    """Load the API documentation to provide context to the LLM."""
    api_doc_path = Path("api_documentation.qmd")
    if api_doc_path.exists():
        with open(api_doc_path, 'r') as f:
            return f.read()
    return ""


def generate_dataset_definition(chatbot, dataset_info, api_documentation, is_dirty=False):
    """
    Use LLM to generate a complete dataset definition JSON.

    Args:
        chatbot: AnthropicChatBot instance
        dataset_info: Dictionary with dataset metadata
        api_documentation: String containing the API documentation
        is_dirty: If True, add missing values and outliers

    Returns:
        Dictionary with dataset definition in the required JSON format
    """
    dataset_type = dataset_info['type']
    domain = dataset_info['domain']
    target = dataset_info['target']
    predictors = dataset_info['predictors']
    description = dataset_info['description']
    dataset_id = dataset_info['id']

    dataset_name = create_dataset_name(dataset_id, target, domain)
    if is_dirty:
        dataset_name += "_dirty"

    # Create prompt for LLM with API documentation context
    prompt = f"""You are generating a synthetic dataset configuration in JSON format for data science instruction.

# API Documentation

{api_documentation}

# Your Task

Read the API documentation above carefully and use it to generate a complete, valid JSON configuration file for the following dataset.

## Dataset Information
- Type: {dataset_type}
- Domain: {domain}
- Target Variable: {target}
- Predictor Variables: {predictors}
- Description: {description}
- Dataset ID: {dataset_id}
- Dataset Name: {dataset_name}

## Requirements
1. Set n_rows to {N_ROWS}
2. Set random_seed to {dataset_id}
3. Parse the predictor variables text and create 4-8 appropriate numeric features with realistic names
4. For the target variable:
   - Name: "{clean_name(target)}"
   - Type: "{"categorical" if dataset_type == "classification" else "float"}"
   - Create an appropriate expression using the features with realistic coefficients
   - Add noise_percent: 2.5
   - For classification: include a "categories" array with exactly 10 meaningful labels
5. Add appropriate correlations between related features (0.3 to 0.7 range)
6. Use realistic distributions for each feature (normal, uniform, weibull, etc.)
7. {"Set missing_rate: 0.02 for ALL features and target" if is_dirty else "Set missing_rate: 0.0 for all features and target"}
8. {"Set outlier_rate: 0.01 and outlier_method: 'extreme_both' for ALL numeric features and numeric targets" if is_dirty else "Set outlier_rate: 0.0 for all features and target"}

## Output Format
Output ONLY valid JSON following the exact structure defined in the API documentation above. Do not include any explanatory text before or after the JSON.
"""

    # Get JSON response from LLM
    response = chatbot.completeAsJSON(prompt)

    # Parse and return JSON
    try:
        dataset_def = json.loads(response)
        return dataset_def
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON for dataset {dataset_id}: {e}")
        print(f"Response was: {response[:500]}")
        return None


def generate_quarto_description(chatbot, dataset_info, clean_name, dirty_name):
    """
    Use LLM to generate Quarto markdown description for a dataset.

    Args:
        chatbot: AnthropicChatBot instance
        dataset_info: Dictionary with dataset metadata
        clean_name: Name of the clean dataset
        dirty_name: Name of the dirty dataset

    Returns:
        String with Quarto markdown content
    """
    dataset_type = dataset_info['type']
    domain = dataset_info['domain']
    target = dataset_info['target']
    predictors = dataset_info['predictors']
    description = dataset_info['description']
    dataset_id = dataset_info['id']

    prompt = f"""Generate a comprehensive Quarto markdown documentation for a synthetic dataset used in data science education.

Dataset Information:
- ID: {dataset_id}
- Type: {dataset_type}
- Domain: {domain}
- Target Variable: {target}
- Predictor Variables: {predictors}
- Description: {description}
- Clean Dataset Name: {clean_name}
- Dirty Dataset Name: {dirty_name}

Requirements:
1. Create an engaging title and overview
2. Invent a realistic use case scenario with background context
3. Describe why this problem is important in the {domain} domain
4. Explain what the target variable means and why it's useful to predict
5. Describe the predictor variables and their relevance
6. Mention that we provide TWO versions:
   - Clean version: No missing values or outliers
   - Dirty version: Contains 2% missing values and 1% outliers (more realistic)
7. Suggest potential machine learning approaches
8. Include data source information (note: this is synthetic data for educational purposes)
9. Provide a dataset summary table with key statistics
10. Use proper Quarto markdown formatting with headers, lists, and code blocks

Output format (Quarto markdown):

---
title: "Dataset {dataset_id}: [Descriptive Title]"
format: html
---

# Overview

[Brief overview paragraph]

# Background and Use Case

[2-3 paragraphs describing a realistic scenario where this dataset would be used]

# Problem Statement

[Description of the predictive problem]

# Target Variable

**{target}**: [Detailed description of what this measures and why it matters]

# Predictor Variables

[List and describe each predictor variable from: {predictors}]

# Dataset Versions

This dataset is provided in two versions:

- **Clean Version** (`{clean_name}.csv`): Ideal for initial model development and learning
- **Dirty Version** (`{dirty_name}.csv`): Contains 2% missing values and 1% outliers, simulating real-world data quality issues

# Suggested Approaches

[List 2-3 machine learning approaches suitable for this problem]

# Dataset Details

| Property | Value |
|----------|-------|
| Dataset ID | {dataset_id} |
| Domain | {domain} |
| Problem Type | {dataset_type.title()} |
| Number of Rows | 1,000 |
| Clean Version | `csv/{clean_name}.csv` |
| Dirty Version | `csv/{dirty_name}.csv` |
| Missing Values (Dirty) | 2% |
| Outliers (Dirty) | 1% |

# Notes

This is a synthetic dataset generated for educational purposes. The relationships between variables have been designed to be realistic and pedagogically useful while maintaining interpretability.

---

Generate engaging, educational content that would be useful for students learning data science.
"""

    response = chatbot.complete(prompt)
    return response


def is_dataset_already_processed(dataset_id, target, domain):
    """
    Check if a dataset has already been processed.

    Args:
        dataset_id: The dataset ID
        target: The target variable name
        domain: The domain name

    Returns:
        Boolean indicating if all required files exist
    """
    clean_name = create_dataset_name(dataset_id, target, domain)
    dirty_name = clean_name + "_dirty"

    # Check for all required files
    clean_def_path = DEFINITIONS_DIR / f"{clean_name}.json"
    dirty_def_path = DEFINITIONS_DIR / f"{dirty_name}.json"
    quarto_path = DESCRIPTIONS_DIR / f"{clean_name}.qmd"

    all_exist = (
        clean_def_path.exists() and
        dirty_def_path.exists() and
        quarto_path.exists()
    )

    return all_exist


def main():
    """Main execution function."""
    print("=" * 80)
    print("Dataset Definition and Documentation Generator")
    print("=" * 80)

    # Load environment variables
    load_dotenv()
    api_key = os.getenv('CLAUDE_API_KEY')

    if not api_key:
        print("Error: CLAUDE_API_KEY not found in .env file")
        return

    # Initialize chatbot
    print("\nInitializing Anthropic ChatBot...")
    chatbot = AnthropicChatBot(
        api_key=api_key,
        model="claude-sonnet-4-20250514",
        use_chat_history=False,
        temperature=0.7,
        max_tokens=8192
    )

    # Setup directories
    print("Setting up output directories...")
    setup_directories()

    # Load dataset configurations
    print("Loading dataset configurations...")
    with open('combined_datasets_json.json', 'r') as f:
        data = json.load(f)

    datasets = data['datasets']
    print(f"Found {len(datasets)} datasets to process")

    # Load API documentation for LLM context
    print("Loading API documentation...")
    api_documentation = load_api_documentation()
    if not api_documentation:
        print("Warning: API documentation not found. LLM may generate invalid JSON.")

    # Track progress
    dataset_metadata = []
    skipped_count = 0

    # Process each dataset
    for i, dataset_info in enumerate(datasets, 1):
        dataset_id = dataset_info['id']
        print(f"\n{'=' * 80}")
        print(f"Processing Dataset {i}/{len(datasets)}: ID={dataset_id}")
        print(f"  Type: {dataset_info['type']}")
        print(f"  Domain: {dataset_info['domain']}")
        print(f"  Target: {dataset_info['target']}")

        # Generate names
        clean_name = create_dataset_name(dataset_id, dataset_info['target'], dataset_info['domain'])
        dirty_name = clean_name + "_dirty"

        # Check if dataset has already been processed
        if is_dataset_already_processed(dataset_id, dataset_info['target'], dataset_info['domain']):
            print(f"  ⏭ Skipping - dataset already processed")
            skipped_count += 1

            # Still add to metadata for index generation
            dataset_metadata.append({
                'id': dataset_id,
                'name': clean_name,
                'domain': dataset_info['domain'],
                'type': dataset_info['type'],
                'target': dataset_info['target'],
                'description': dataset_info['description']
            })
            continue

        # Generate clean definition
        print(f"  Generating clean definition...")
        clean_def = generate_dataset_definition(chatbot, dataset_info, api_documentation, is_dirty=False)
        if clean_def:
            clean_path = DEFINITIONS_DIR / f"{clean_name}.json"
            with open(clean_path, 'w') as f:
                json.dump(clean_def, f, indent=2)
            print(f"  ✓ Saved: {clean_path}")
        else:
            print(f"  ✗ Failed to generate clean definition")
            continue

        # Generate dirty definition
        print(f"  Generating dirty definition...")
        dirty_def = generate_dataset_definition(chatbot, dataset_info, api_documentation, is_dirty=True)
        if dirty_def:
            dirty_path = DEFINITIONS_DIR / f"{dirty_name}.json"
            with open(dirty_path, 'w') as f:
                json.dump(dirty_def, f, indent=2)
            print(f"  ✓ Saved: {dirty_path}")
        else:
            print(f"  ✗ Failed to generate dirty definition")
            continue

        # Validate definitions can be loaded by Dataset class
        print(f"  Validating definitions...")
        try:
            # Validate clean definition
            clean_dataset = Dataset(clean_def)
            clean_validation = clean_dataset.validate()
            if clean_validation['valid']:
                print(f"  ✓ Clean definition is valid")
            else:
                print(f"  ✗ Clean definition has errors:")
                for error in clean_validation['errors']:
                    print(f"      - {error}")

            # Validate dirty definition
            dirty_dataset = Dataset(dirty_def)
            dirty_validation = dirty_dataset.validate()
            if dirty_validation['valid']:
                print(f"  ✓ Dirty definition is valid")
            else:
                print(f"  ✗ Dirty definition has errors:")
                for error in dirty_validation['errors']:
                    print(f"      - {error}")
        except Exception as e:
            print(f"  ✗ Failed to validate definitions: {e}")
            import traceback
            traceback.print_exc()

        # Generate Quarto documentation
        print(f"  Generating Quarto documentation...")
        quarto_content = generate_quarto_description(chatbot, dataset_info, clean_name, dirty_name)
        if quarto_content:
            quarto_path = DESCRIPTIONS_DIR / f"{clean_name}.qmd"
            with open(quarto_path, 'w') as f:
                f.write(quarto_content)
            print(f"  ✓ Saved: {quarto_path}")
        else:
            print(f"  ✗ Failed to generate Quarto documentation")
            continue

        # Store metadata for index
        dataset_metadata.append({
            'id': dataset_id,
            'name': clean_name,
            'domain': dataset_info['domain'],
            'type': dataset_info['type'],
            'target': dataset_info['target'],
            'description': dataset_info['description']
        })

        print(f"  ✓ Dataset {dataset_id} complete")

    # Generate index.qmd
    print(f"\n{'=' * 80}")
    print("Generating index.qmd...")
    generate_index_qmd(dataset_metadata)
    print("✓ index.qmd created")

    # Generate _quarto.yml
    print("Generating _quarto.yml...")
    generate_quarto_yml(dataset_metadata)
    print("✓ _quarto.yml created")

    print(f"\n{'=' * 80}")
    print("Processing Complete!")
    print(f"{'=' * 80}")
    print(f"Summary:")
    print(f"  - Total datasets in config: {len(datasets)}")
    print(f"  - Skipped (already processed): {skipped_count}")
    print(f"  - Newly generated: {len(dataset_metadata) - skipped_count}")
    print(f"\nGenerated:")
    print(f"  - {len(dataset_metadata) * 2} dataset definitions (clean + dirty)")
    print(f"  - {len(dataset_metadata)} Quarto markdown files")
    print(f"  - 1 index.qmd")
    print(f"  - 1 _quarto.yml")
    print(f"\nOutput directory: {OUTPUT_DIR}")
    print(f"\nNext steps:")
    print(f"  1. Review generated definitions in: {DEFINITIONS_DIR}")
    print(f"  2. Review validation results above")
    print(f"  3. Generate CSV files by running dataset_generator.py on each JSON")
    print(f"  4. Build Quarto book: cd {OUTPUT_DIR} && quarto render")


def generate_index_qmd(dataset_metadata):
    """Generate index.qmd with links to all dataset descriptions."""

    # Group by domain
    domains = {}
    for ds in dataset_metadata:
        domain = ds['domain']
        if domain not in domains:
            domains[domain] = []
        domains[domain].append(ds)

    # Sort domains and datasets
    sorted_domains = sorted(domains.keys())

    content = """---
title: "ADS Datasets - Synthetic Datasets for Data Science Education"
format: html
---

# Welcome

This collection contains 100 synthetic datasets designed for teaching and learning data science concepts. Each dataset is provided in two versions:

- **Clean Version**: No missing values or outliers - ideal for initial model development
- **Dirty Version**: Contains 2% missing values and 1% outliers - simulates real-world data challenges

All datasets are synthetic and generated for educational purposes.

# Dataset Catalog

"""

    # Add table of contents by domain
    for domain in sorted_domains:
        content += f"\n## {domain}\n\n"
        datasets = sorted(domains[domain], key=lambda x: x['id'])

        for ds in datasets:
            ds_type_icon = "📊" if ds['type'] == "regression" else "🏷️"
            content += f"- {ds_type_icon} [{ds['id']:03d}: {ds['target']}](dataset_descriptions/{ds['name']}.qmd)\n"

    content += """

# Dataset Statistics

"""

    # Add summary statistics
    total_datasets = len(dataset_metadata)
    regression_count = sum(1 for ds in dataset_metadata if ds['type'] == 'regression')
    classification_count = total_datasets - regression_count

    content += f"""
| Metric | Count |
|--------|-------|
| Total Datasets | {total_datasets} |
| Regression Problems | {regression_count} |
| Classification Problems | {classification_count} |
| Domains | {len(sorted_domains)} |
| Total Rows (all datasets) | {total_datasets * N_ROWS * 2:,} |

# Using These Datasets

1. **Choose a Dataset**: Browse by domain or problem type
2. **Download CSV Files**: Access from `csv/` directory
3. **Start with Clean**: Use clean version for initial exploration
4. **Practice with Dirty**: Test data cleaning and outlier detection on dirty version
5. **Compare Results**: See how data quality affects model performance

# Domains Covered

"""

    for domain in sorted_domains:
        count = len(domains[domain])
        reg_count = sum(1 for ds in domains[domain] if ds['type'] == 'regression')
        cls_count = count - reg_count
        content += f"- **{domain}**: {count} datasets ({reg_count} regression, {cls_count} classification)\n"

    content += """

# About

These datasets were generated using the AI Dataset Generator tool, which creates realistic synthetic data for educational purposes. Each dataset includes:

- Realistic feature distributions
- Meaningful correlations between variables
- Interpretable target relationships
- Appropriate levels of noise and complexity

For questions or feedback, please see the project repository.
"""

    # Write index.qmd
    index_path = OUTPUT_DIR / "index.qmd"
    with open(index_path, 'w') as f:
        f.write(content)


def generate_quarto_yml(dataset_metadata):
    """Generate _quarto.yml for Quarto book format."""

    # Group by domain
    domains = {}
    for ds in dataset_metadata:
        domain = ds['domain']
        if domain not in domains:
            domains[domain] = []
        domains[domain].append(ds)

    # Sort
    sorted_domains = sorted(domains.keys())

    yml_content = """project:
  type: book
  output-dir: _book

book:
  title: "ADS Datasets"
  subtitle: "Synthetic Datasets for Data Science Education"
  author: "AI Dataset Generator"
  date: today

  chapters:
    - index.qmd
    - part: "Datasets by Domain"
      chapters:
"""

    # Add chapters by domain
    for domain in sorted_domains:
        yml_content += f"        - part: \"{domain}\"\n"
        yml_content += f"          chapters:\n"

        datasets = sorted(domains[domain], key=lambda x: x['id'])
        for ds in datasets:
            yml_content += f"            - dataset_descriptions/{ds['name']}.qmd\n"

    yml_content += """

format:
  html:
    theme: cosmo
    toc: true
    toc-depth: 2
    number-sections: false
    code-fold: false
    code-tools: true
    css: styles.css

execute:
  echo: false
  warning: false
"""

    # Write _quarto.yml
    yml_path = OUTPUT_DIR / "_quarto.yml"
    with open(yml_path, 'w') as f:
        f.write(yml_content)


if __name__ == "__main__":
    main()

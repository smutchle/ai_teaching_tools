#!/usr/bin/env python3
"""
Generate CSV files from all dataset definitions with self-healing error recovery.

This script:
1. Reads all dataset definition JSON files from ./definitions/
2. Generates CSV files using DatasetGenerator
3. Outputs CSVs to ./csv/
4. Uses LLM to automatically fix errors in dataset definitions when generation fails
5. Tracks progress and provides detailed reporting
"""

import json
import os
import sys
import traceback
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to Python path for imports
parent_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent_dir))

from AnthropicChatBot import AnthropicChatBot
from dataset import Dataset
from dataset_generator import DatasetGenerator


# Configuration
DEFINITIONS_DIR = Path("./definitions")
OUTPUT_DIR = Path("./csv")
MAX_HEALING_ATTEMPTS = 2  # Maximum number of self-healing attempts per dataset


def setup_directories():
    """Create necessary directories."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_api_documentation():
    """Load the API documentation to provide context to the LLM."""
    api_doc_path = Path("../api_documentation.qmd")
    if api_doc_path.exists():
        with open(api_doc_path, 'r') as f:
            return f.read()
    return ""


def is_csv_already_generated(definition_path: Path) -> bool:
    """
    Check if CSV has already been generated for this definition.

    Args:
        definition_path: Path to the JSON definition file

    Returns:
        Boolean indicating if the output CSV file already exists
    """
    # Load the definition to get the dataset name
    try:
        with open(definition_path, 'r') as f:
            data = json.load(f)
            dataset_name = data['dataset_config']['name']
            csv_path = OUTPUT_DIR / f"{dataset_name}.csv"
            return csv_path.exists()
    except Exception:
        return False


def attempt_csv_generation(definition_path: Path) -> tuple[bool, str, Exception]:
    """
    Attempt to generate CSV from a dataset definition.

    Args:
        definition_path: Path to the JSON definition file

    Returns:
        Tuple of (success: bool, csv_path: str, error: Exception or None)
    """
    try:
        # Load definition
        with open(definition_path, 'r') as f:
            dataset_def = json.load(f)

        # Create Dataset object
        dataset = Dataset(dataset_def)

        # Validate dataset
        validation = dataset.validate()
        if not validation['valid']:
            error_msg = "Validation errors:\n" + "\n".join(validation['errors'])
            raise ValueError(error_msg)

        # Generate dataset
        generator = DatasetGenerator(dataset)

        # Temporarily change output directory
        original_generate = generator.generate
        def generate_with_custom_path():
            # Call original generate but change the output path
            import pandas as pd
            import numpy as np

            # Run the generation logic (everything except saving)
            if dataset.random_seed is not None:
                np.random.seed(dataset.random_seed)
                generator.rng = np.random.RandomState(dataset.random_seed)
            else:
                generator.rng = np.random.RandomState()

            for feature in dataset.features:
                if feature['data_type'] == 'categorical':
                    generator.categorical_features.add(feature['name'])
                if feature.get('distribution', {}).get('type') == 'sequential_datetime':
                    generator.datetime_features.add(feature['name'])

            generator._generate_features()
            generator._generate_lagged_features()
            generator._apply_correlations()

            if len(generator.datetime_features) > 0:
                generator._smooth_time_series_features()

            generator._apply_outliers()
            generator._convert_int_types()
            generator._apply_categorical_conversions()
            generator._apply_missing_data()
            generator._generate_target()

            # Create DataFrame
            lagged_feature_names = generator._get_lagged_feature_names()
            columns_to_include = [col for col in generator.data.keys() if col not in lagged_feature_names]
            df = pd.DataFrame({col: generator.data[col] for col in columns_to_include})

            # Save to custom path
            output_path = OUTPUT_DIR / f"{dataset.name}.csv"
            df.to_csv(output_path, index=False)

            return output_path

        csv_path = generate_with_custom_path()

        return True, str(csv_path), None

    except Exception as e:
        return False, "", e


def self_heal_definition(definition_path: Path, error: Exception, api_documentation: str, chatbot: AnthropicChatBot) -> bool:
    """
    Use LLM to fix errors in the dataset definition.

    Args:
        definition_path: Path to the JSON definition file
        error: The error that occurred
        api_documentation: API documentation string
        chatbot: AnthropicChatBot instance

    Returns:
        Boolean indicating if healing was successful
    """
    print(f"    🔧 Attempting self-healing...")

    try:
        # Load the current definition
        with open(definition_path, 'r') as f:
            current_def = json.load(f)

        # Create healing prompt
        error_message = str(error)
        error_traceback = traceback.format_exc()

        prompt = f"""You are fixing a dataset configuration JSON that caused an error during generation.

# API Documentation

{api_documentation}

# Current Dataset Definition

```json
{json.dumps(current_def, indent=2)}
```

# Error Encountered

{error_message}

# Full Traceback

{error_traceback}

# Your Task

Analyze the error and fix the dataset definition JSON. Common issues include:
1. Invalid expressions referencing non-existent features
2. Missing required fields
3. Invalid distribution parameters
4. Correlation matrix issues
5. Type mismatches
6. Invalid data_type or distribution type combinations

Output ONLY the corrected JSON following the exact structure in the API documentation. Do not include any explanatory text before or after the JSON.
"""

        # Get fixed JSON from LLM
        response = chatbot.completeAsJSON(prompt)

        # Parse and validate the fixed JSON
        try:
            fixed_def = json.loads(response)

            # Basic sanity check
            if 'dataset_config' not in fixed_def:
                print(f"    ✗ Self-healing failed: LLM response missing dataset_config")
                return False

            # Save backup of original
            backup_path = definition_path.with_suffix('.json.backup')
            with open(backup_path, 'w') as f:
                json.dump(current_def, f, indent=2)

            # Save fixed definition
            with open(definition_path, 'w') as f:
                json.dump(fixed_def, f, indent=2)

            print(f"    ✓ Self-healing applied (backup saved to {backup_path.name})")
            return True

        except json.JSONDecodeError as e:
            print(f"    ✗ Self-healing failed: LLM returned invalid JSON: {e}")
            return False

    except Exception as e:
        print(f"    ✗ Self-healing failed with exception: {e}")
        return False


def process_dataset(definition_path: Path, api_documentation: str, chatbot: AnthropicChatBot) -> dict:
    """
    Process a single dataset definition with self-healing.

    Args:
        definition_path: Path to the JSON definition file
        api_documentation: API documentation string
        chatbot: AnthropicChatBot instance for self-healing

    Returns:
        Dictionary with processing results
    """
    result = {
        'path': str(definition_path),
        'name': definition_path.stem,
        'success': False,
        'skipped': False,
        'csv_path': None,
        'error': None,
        'healing_attempts': 0,
        'healed': False
    }

    # Check if already generated
    if is_csv_already_generated(definition_path):
        result['skipped'] = True
        result['success'] = True
        return result

    # Attempt generation (with self-healing retries)
    for attempt in range(MAX_HEALING_ATTEMPTS + 1):
        success, csv_path, error = attempt_csv_generation(definition_path)

        if success:
            result['success'] = True
            result['csv_path'] = csv_path
            if attempt > 0:
                result['healed'] = True
            return result

        # If failed and we have healing attempts left
        if attempt < MAX_HEALING_ATTEMPTS:
            result['healing_attempts'] += 1
            healed = self_heal_definition(definition_path, error, api_documentation, chatbot)

            if not healed:
                # Healing failed, give up
                result['error'] = str(error)
                return result
        else:
            # No more healing attempts
            result['error'] = str(error)
            return result

    return result


def main():
    """Main execution function."""
    print("=" * 80)
    print("CSV Dataset Generator with Self-Healing")
    print("=" * 80)

    # Load environment variables from parent directory
    env_path = Path(__file__).resolve().parent.parent / '.env'
    load_dotenv(env_path)
    api_key = os.getenv('CLAUDE_API_KEY')

    if not api_key:
        print("Error: CLAUDE_API_KEY not found in .env file")
        return

    # Initialize chatbot for self-healing
    print("\nInitializing Anthropic ChatBot for self-healing...")
    chatbot = AnthropicChatBot(
        api_key=api_key,
        model="claude-sonnet-4-20250514",
        use_chat_history=False,
        temperature=0.3,  # Lower temperature for more deterministic fixes
        max_tokens=8192
    )

    # Setup directories
    print("Setting up output directories...")
    setup_directories()

    # Load API documentation
    print("Loading API documentation...")
    api_documentation = load_api_documentation()
    if not api_documentation:
        print("Warning: API documentation not found. Self-healing may be less effective.")

    # Find all definition files
    if not DEFINITIONS_DIR.exists():
        print(f"Error: Definitions directory not found: {DEFINITIONS_DIR}")
        return

    definition_files = sorted(DEFINITIONS_DIR.glob("*.json"))
    if not definition_files:
        print(f"Error: No JSON definition files found in {DEFINITIONS_DIR}")
        return

    print(f"Found {len(definition_files)} dataset definitions to process\n")

    # Track results
    results = {
        'total': len(definition_files),
        'success': 0,
        'skipped': 0,
        'failed': 0,
        'healed': 0,
        'failed_datasets': []
    }

    # Process each definition
    for i, def_path in enumerate(definition_files, 1):
        print(f"\n{'=' * 80}")
        print(f"Processing {i}/{len(definition_files)}: {def_path.name}")

        result = process_dataset(def_path, api_documentation, chatbot)

        if result['skipped']:
            print(f"  ⏭ Skipped - CSV already exists")
            results['skipped'] += 1
            results['success'] += 1
        elif result['success']:
            if result['healed']:
                print(f"  ✓ Success after self-healing ({result['healing_attempts']} attempt(s))")
                print(f"  → {result['csv_path']}")
                results['healed'] += 1
            else:
                print(f"  ✓ Success")
                print(f"  → {result['csv_path']}")
            results['success'] += 1
        else:
            print(f"  ✗ Failed after {result['healing_attempts']} healing attempt(s)")
            print(f"  Error: {result['error']}")
            results['failed'] += 1
            results['failed_datasets'].append({
                'name': result['name'],
                'path': result['path'],
                'error': result['error']
            })

    # Print summary
    print(f"\n{'=' * 80}")
    print("Processing Complete!")
    print(f"{'=' * 80}")
    print(f"Summary:")
    print(f"  Total datasets: {results['total']}")
    print(f"  Successful: {results['success']}")
    print(f"  - Skipped (already existed): {results['skipped']}")
    print(f"  - Generated: {results['success'] - results['skipped']}")
    print(f"  - Self-healed: {results['healed']}")
    print(f"  Failed: {results['failed']}")

    if results['failed_datasets']:
        print(f"\nFailed Datasets:")
        for failed in results['failed_datasets']:
            print(f"  - {failed['name']}")
            print(f"    Path: {failed['path']}")
            print(f"    Error: {failed['error'][:100]}...")

    print(f"\nOutput directory: {OUTPUT_DIR}")
    print(f"CSV files generated: {results['success'] - results['skipped']}")

    if results['healed'] > 0:
        print(f"\n💡 {results['healed']} dataset(s) were automatically fixed using self-healing")
        print(f"   Original definitions backed up with .backup extension")


if __name__ == "__main__":
    main()

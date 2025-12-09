# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a synthetic dataset generator for data science education. It creates realistic datasets with configurable features, distributions, correlations, missing data, outliers, and time series characteristics. The tool includes both a Streamlit web UI and batch processing scripts for generating large collections of datasets.

## Running the Application

```bash
# Run the Streamlit web application
streamlit run app_dataset.py

# Generate dataset definitions and documentation (batch)
python ads_datasets/generate_dataset_definitions.py

# Generate CSV files from definitions with self-healing
cd ads_datasets && python generate_all_csvs.py

# Add download links to dataset documentation
python add_download_links.py

# Render Quarto documentation
cd ads_datasets && quarto render
```

## Architecture

**Core Classes:**
- `Dataset` (dataset.py): Validates JSON configuration schema - features, distributions, correlations, target expressions
- `DatasetGenerator` (dataset_generator.py): Generates synthetic data from Dataset specifications using NumPy/Pandas

**LLM Integration:**
- `OllamaChatBot` (OllamaChatBot.py): Local LLM via Ollama API
- `AnthropicChatBot` (AnthropicChatBot.py): Claude API client with retry logic

**Applications:**
- `app_dataset.py`: Streamlit UI with chat-based dataset creation, JSON editor, and description generation
- `ads_datasets/generate_dataset_definitions.py`: Batch generates 100+ dataset definitions using LLM
- `ads_datasets/generate_all_csvs.py`: Generates CSVs with LLM-powered self-healing for validation errors

## Key Concepts

**Dataset Configuration JSON Structure:**
```json
{
  "dataset_config": {
    "name": "string",
    "n_rows": integer,
    "random_seed": integer,
    "features": [...],
    "correlations": [...],
    "target": {...}
  }
}
```

**Distribution Types:** uniform, normal, weibull, random_walk, sequential, sequential_datetime

**Time Series Features:**
- `sequential_datetime` distribution for date columns
- `lags` array on features creates auto-generated lag variables (e.g., `price_lag1`)
- `seasonality_multipliers` and `secondary_seasonality_multipliers` on target
- Difference-based generation produces smooth time series (not jumpy values)

**Categorical Features:** Must have exactly 10 categories (mapped to deciles)

**Target Expression:** Python expression using feature names and numpy (`np`), e.g., `"feature1 * 2 + np.sqrt(feature2)"`

## Environment Configuration

Create `.env` file with:
```
CLAUDE_API_KEY=your_key
ANTHROPIC_MODEL=claude-sonnet-4-20250514
OLLAMA_MODEL=gemma3:27b
OLLAMA_END_POINT=http://localhost:11434
```

## File Organization

- `datasets/`: Generated dataset JSON configs and CSVs from web UI
- `ads_datasets/definitions/`: Batch-generated JSON definitions
- `ads_datasets/csv/`: Batch-generated CSV files
- `ads_datasets/dataset_descriptions/`: Quarto documentation per dataset
- `api_documentation.qmd`: JSON schema reference (used as LLM context)

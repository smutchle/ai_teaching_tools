# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Synthetic dataset generator for data science education. A JSON specification describes features, distributions, correlations, missing/outlier rates, lags, and a target expression; the generator produces a reproducible CSV. A Streamlit app wraps this with a chat-driven config builder, JSON editor, and LLM-powered self-healing for validation errors.

## Running the Application

```bash
# Local dev
streamlit run app_dataset.py

# Background (writes to app_dataset.log, port 8581)
./run_in_background.sh

# Render the JSON-schema reference docs (uses Quarto)
quarto render api_documentation.qmd
quarto render user_documentation.qmd
```

Dependencies live in the parent project's `requirements.txt` (`../requirements.txt`) — there's no per-project requirements file here.

## Environment Configuration

`.env` is loaded by `app_dataset.py` via `python-dotenv`:

```
CLAUDE_API_KEY=<key>
ANTHROPIC_MODEL=claude-sonnet-4-6
OLLAMA_MODEL=gemma3:27b
OLLAMA_END_POINT=http://localhost:11434
```

The user can override the Claude key per-session in the Streamlit sidebar.

## Architecture

Two-layer core, plus a Streamlit UI:

- **`Dataset` (dataset.py)** — pure validation. Takes a dict with a `dataset_config` root, exposes `validate()` returning `{valid, errors}`. Validates feature shape, distribution params, expression variable references, correlation references, and rate ranges. Does NOT generate data.
- **`DatasetGenerator` (dataset_generator.py)** — consumes a `Dataset` and produces a CSV in `./datasets/<name>.csv`. The pipeline order in `generate()` matters and is non-obvious:
  1. Generate raw features (difference-based path for time series, direct sampling otherwise)
  2. Generate lagged copies (`<name>_lag<k>`)
  3. Apply correlations (Cholesky → rank-based rewrite to preserve marginals)
  4. EMA-smooth time series features (only if a `sequential_datetime` feature exists)
  5. Apply outliers (IQR-based, skips categorical/datetime)
  6. Convert int types
  7. Convert categorical features to labels via `pd.qcut` into 10 deciles
  8. Apply missing data
  9. Evaluate the target expression
- **LLM clients** — `AnthropicChatBot.py` (Claude with 529 retry/backoff) and `OllamaChatBot.py` (local Ollama, chat vs. generate endpoint based on history). Both expose `complete()` and `completeAsJSON()` (which strips ```json fences).
- **`app_dataset.py`** — Streamlit UI. Three tabs (Chat Assistant, JSON Editor, Dataset Description). Chat walks the user through a fixed `QUESTIONS` list, then sends the answers + `api_documentation.qmd` to the LLM to produce JSON. On generation failure, "Fix Error with AI" round-trips the broken JSON + error message back through the LLM.

## Key Concepts (Easy to Get Wrong)

**Categorical features must have exactly 10 categories.** They're bucketed via `pd.qcut(values, q=10)` and the array indexes the `categories` list. This is enforced in `Dataset._validate_feature` and again in `_validate_target` for categorical targets.

**Categorical features in target expressions evaluate as numeric deciles (0–9), not labels.** Before categorical conversion overwrites the column with string labels, `DatasetGenerator` captures the decile index in `self.categorical_deciles[name]` and exposes it under the feature's name in the target-expression namespace. NaNs become 4. So `target = "tier * 100"` works even when `tier` is categorical.

**`lags: [1, 2, 3]` on a feature auto-generates `<name>_lag1`, `_lag2`, `_lag3`.** Lagged columns are available in the target expression but are excluded from the output CSV (`_get_lagged_feature_names`). Reference a non-existent lag in the expression and validation fails.

**Time series detection is implicit: any feature with `sequential_datetime` distribution flips the whole dataset into time-series mode.** In that mode, non-datetime non-sequential features are generated via `_generate_time_series_distribution` (start value from the distribution, then accumulated small differences — 2% of range for uniform, 5% of std for normal) and post-processed with EMA smoothing rescaled back to the original mean/std. Cross-sectional generation is the direct-sample path.

**Correlations vs. time series smoothness is a known trade-off.** Correlations rewrite features by rank-matching to a Cholesky-correlated multivariate normal, which disrupts temporal smoothness; the EMA pass partially restores it but correlations are then only approximately preserved (~±10–15%). See `CHANGES_SUMMARY.md` for details.

**Target expression namespace** is `{np, <numeric_feature>: float_array, <categorical_feature>: decile_array}`. Datetime features are excluded. Use plain numpy via `np.` prefix (`np.sqrt`, `np.where`, etc.).

**Seasonality** — `seasonality_multipliers` and `secondary_seasonality_multipliers` on the target are arrays whose length equals the periodicity (12 for monthly, 7 for weekly, 24 for hourly). Both are multiplicative on the target before noise.

## File Organization

- `dataset.py`, `dataset_generator.py` — core
- `app_dataset.py` — Streamlit UI (single file, ~800 lines)
- `AnthropicChatBot.py`, `OllamaChatBot.py` — LLM clients
- `api_documentation.qmd` / `.pdf` — JSON schema reference; **the .qmd is fed into the LLM as context** when generating or repairing configs, so keep it in sync with `Dataset._validate_*` rules.
- `user_documentation.qmd` / `.pdf` — end-user guide for the Streamlit app
- `combined_datasets_json.json` — example combined config bundle
- `CHANGES_SUMMARY.md` — historical notes on the time-series difference-based generation and secondary seasonality additions; useful when touching `_generate_time_series_distribution` or `_smooth_time_series_features`
- `datasets/` — generated `.json` configs, `.csv` outputs, and `.qmd` descriptions from the UI (gitignored)
- `add_download_links.py` — utility for a separate `ads_datasets/dataset_descriptions/` Quarto site; the target directory does not exist in this repo and the script no-ops here.

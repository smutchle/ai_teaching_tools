"""
Streamlit application for managing and generating datasets.
"""
import streamlit as st
import json
import os
from pathlib import Path
from dataset import Dataset
from dataset_generator import DatasetGenerator

# Configuration
DATASETS_DIR = Path("./datasets")
DATASETS_DIR.mkdir(exist_ok=True)

def load_dataset_list():
    """Load list of available dataset JSON files."""
    return sorted([f.stem for f in DATASETS_DIR.glob("*.json")])

def load_dataset_config(name):
    """Load a dataset configuration from JSON file."""
    filepath = DATASETS_DIR / f"{name}.json"
    with open(filepath, 'r') as f:
        return json.load(f)

def save_dataset_config(name, config):
    """Save a dataset configuration to JSON file."""
    filepath = DATASETS_DIR / f"{name}.json"
    with open(filepath, 'w') as f:
        json.dump(config, f, indent=2)

def delete_dataset_config(name):
    """Delete a dataset configuration JSON file."""
    filepath = DATASETS_DIR / f"{name}.json"
    if filepath.exists():
        filepath.unlink()

def generate_csv(dataset):
    """Generate CSV from dataset configuration."""
    generator = DatasetGenerator(dataset)
    csv_path = generator.generate()
    return csv_path

# Streamlit UI
st.set_page_config(page_title="Dataset Generator", layout="wide")
st.title("📊 Dataset Generator for Data Science Instruction")

# Sidebar for dataset management
st.sidebar.header("Dataset Management")

# Initialize session state
if 'current_config' not in st.session_state:
    st.session_state.current_config = None
if 'config_text' not in st.session_state:
    st.session_state.config_text = ""
if 'selected_dataset' not in st.session_state:
    st.session_state.selected_dataset = None

# Dataset selection
available_datasets = load_dataset_list()
selected = st.sidebar.selectbox(
    "Select Existing Dataset",
    [""] + available_datasets,
    key="dataset_selector"
)

# Load selected dataset
if selected and selected != st.session_state.selected_dataset:
    st.session_state.selected_dataset = selected
    config = load_dataset_config(selected)
    st.session_state.config_text = json.dumps(config, indent=2)
    st.session_state.current_config = config

# Action buttons
col1, col2, col3 = st.sidebar.columns(3)

if col1.button("➕ New", use_container_width=True):
    template = {
        "dataset_config": {
            "name": "new_dataset",
            "description": "New dataset description",
            "random_seed": 42,
            "n_rows": 1000,
            "correlations": [],
            "features": [
                {
                    "name": "feature1",
                    "description": "First feature",
                    "data_type": "float",
                    "distribution": {
                        "type": "normal",
                        "mean": 0.0,
                        "std": 1.0
                    },
                    "missing_rate": 0.0,
                    "outlier_rate": 0.0
                }
            ],
            "target": {
                "name": "target",
                "description": "Target variable",
                "data_type": "float",
                "expression": "feature1 * 2",
                "noise_percent": 5.0
            }
        }
    }
    st.session_state.config_text = json.dumps(template, indent=2)
    st.session_state.selected_dataset = None
    st.rerun()

if col2.button("💾 Save", use_container_width=True):
    try:
        config = json.loads(st.session_state.config_text)
        name = config['dataset_config']['name']
        save_dataset_config(name, config)
        st.sidebar.success(f"✅ Saved '{name}'")
        st.session_state.selected_dataset = name
        st.rerun()
    except json.JSONDecodeError as e:
        st.sidebar.error(f"❌ Invalid JSON: {str(e)}")
    except Exception as e:
        st.sidebar.error(f"❌ Error: {str(e)}")

if col3.button("🗑️ Delete", use_container_width=True, disabled=not st.session_state.selected_dataset):
    if st.session_state.selected_dataset:
        delete_dataset_config(st.session_state.selected_dataset)
        st.sidebar.success(f"🗑️ Deleted '{st.session_state.selected_dataset}'")
        st.session_state.selected_dataset = None
        st.session_state.config_text = ""
        st.rerun()

st.sidebar.divider()

# Generate CSV button
if st.sidebar.button("🎲 Generate CSV", type="primary", use_container_width=True):
    try:
        config = json.loads(st.session_state.config_text)
        dataset = Dataset(config)
        csv_path = generate_csv(dataset)
        st.sidebar.success(f"✅ Generated: {csv_path}")
        
        # Offer download
        with open(csv_path, 'r') as f:
            st.sidebar.download_button(
                label="⬇️ Download CSV",
                data=f.read(),
                file_name=csv_path.name,
                mime="text/csv",
                use_container_width=True
            )
    except json.JSONDecodeError as e:
        st.sidebar.error(f"❌ Invalid JSON: {str(e)}")
    except Exception as e:
        st.sidebar.error(f"❌ Generation Error: {str(e)}")

# Main editor area
st.header("Configuration Editor")
st.markdown("Edit the JSON configuration below. Use the documentation as reference.")

# Text area for editing config
new_config_text = st.text_area(
    "Dataset Configuration (JSON)",
    value=st.session_state.config_text,
    height=600,
    key="config_editor"
)

# Update session state when text changes
if new_config_text != st.session_state.config_text:
    st.session_state.config_text = new_config_text

# Validation area
st.header("Configuration Validation")
if st.session_state.config_text:
    try:
        config = json.loads(st.session_state.config_text)
        dataset = Dataset(config)
        validation_results = dataset.validate()
        
        if validation_results['valid']:
            st.success("✅ Configuration is valid!")
            
            # Show summary
            with st.expander("📋 Dataset Summary"):
                dc = config['dataset_config']
                st.write(f"**Name:** {dc['name']}")
                st.write(f"**Description:** {dc.get('description', 'N/A')}")
                st.write(f"**Rows:** {dc['n_rows']}")
                st.write(f"**Random Seed:** {dc.get('random_seed', 'None (random)')}")
                st.write(f"**Features:** {len(dc['features'])}")
                st.write(f"**Correlations:** {len(dc.get('correlations', []))}")
                st.write(f"**Target:** {dc['target']['name']} ({dc['target']['data_type']})")
        else:
            st.error("❌ Configuration has errors:")
            for error in validation_results['errors']:
                st.error(f"  • {error}")
                
    except json.JSONDecodeError as e:
        st.warning(f"⚠️ Invalid JSON syntax: {str(e)}")
    except Exception as e:
        st.error(f"❌ Validation error: {str(e)}")

# Documentation section
with st.expander("📖 View Documentation"):
    st.markdown("""
    ## Quick Reference
    
    ### Distribution Types
    - **uniform**: `{"type": "uniform", "min": 0, "max": 1}`
    - **normal**: `{"type": "normal", "mean": 0, "std": 1, "min_clip": null, "max_clip": null}`
    - **weibull**: `{"type": "weibull", "shape": 1.5, "scale": 1.0, "location": 0}`
    - **random_walk**: `{"type": "random_walk", "start": 100, "step_size": 1, "drift": 0}`
    - **sequential**: `{"type": "sequential", "start": 1, "step": 1}`
    
    ### Data Types
    - **float**: Continuous numeric
    - **int**: Integer numeric
    - **categorical**: Must include 10 category labels
    
    ### Outlier Methods
    - **extreme_high**: High outliers only
    - **extreme_low**: Low outliers only
    - **extreme_both**: Both high and low
    
    ### Target Expression
    Use feature names and NumPy functions:
    - `"3*feature1 + 2*feature2 - 10"`
    - `"np.exp(feature1) + np.log(feature2 + 1)"`
    - `"1 / (1 + np.exp(-(feature1 - 5)))"`  # Logistic
    
    For full documentation, see the configuration guide.
    """)
    
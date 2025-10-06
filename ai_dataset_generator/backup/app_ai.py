"""
Streamlit application for managing and generating datasets.
"""
import streamlit as st
import json
import os
from pathlib import Path
from dataset import Dataset
from dataset_generator import DatasetGenerator
from OllamaChatBot import OllamaChatBot

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

def load_documentation():
    """Load documentation content from documentation.qmd file."""
    doc_path = Path("./documentation.qmd")
    if doc_path.exists():
        with open(doc_path, 'r') as f:
            return f.read()
    return ""

def generate_dataset_from_chat(chat_log, documentation):
    """Generate JSON configuration from chat conversation using LLM."""
    chatbot = OllamaChatBot(
        model="gemma3:27b",
        end_point_url="http://localhost:11434",
        temperature=0.0,
        keep_history=False
    )
    
    prompt = f"""You are a dataset configuration generator. Based on the user's requirements and the documentation provided, generate a valid JSON configuration for a synthetic dataset.

DOCUMENTATION:
{documentation}

USER CONVERSATION:
{chat_log}

CRITICAL REQUIREMENTS:
1. Categorical features must use one of these distribution types: uniform, normal, weibull, random_walk, or sequential
2. If the target is categorical, it MUST have exactly 10 category labels in the "categories" array
3. Target expressions can ONLY use NUMERIC features (float or int), NOT categorical features
4. For categorical targets, repeat category labels to create desired class distributions (e.g., ["No", "No", "No", "No", "No", "No", "No", "Yes", "Yes", "Yes"] for 30% "Yes")

INSTRUCTIONS:
1. Generate a complete, valid JSON configuration that matches the schema described in the documentation
2. Use the user's answers to determine:
   - Dataset name and description
   - Number of rows
   - Target variable data type (categorical, int, or float)
   - Number of categorical features
   - Number of numeric features (int and float)
   - Missing rates for features
   - Outlier rates for features
   - Correlations between predictors (if requested)
   - Noise percentage for target calculation
3. Create meaningful feature names and distributions appropriate for the dataset domain
4. For categorical features, use normal/uniform distributions, then add exactly 10 labels in the "categories" array
5. Ensure the target expression ONLY references numeric features (never categorical ones)
6. If target is categorical, provide exactly 10 category labels
7. Return ONLY the JSON configuration, no additional text or explanation

Generate the JSON configuration now:"""
    
    response = chatbot.completeAsJSON(prompt)
    return response

# Streamlit UI
st.set_page_config(page_title="Dataset Generator", layout="wide")
st.title("📊 Dataset Generator for Data Science Instruction")

# Initialize session state
if 'current_config' not in st.session_state:
    st.session_state.current_config = None
if 'config_text' not in st.session_state:
    st.session_state.config_text = ""
if 'selected_dataset' not in st.session_state:
    st.session_state.selected_dataset = None
if 'chat_messages' not in st.session_state:
    st.session_state.chat_messages = []
if 'current_question' not in st.session_state:
    st.session_state.current_question = 0
if 'chat_answers' not in st.session_state:
    st.session_state.chat_answers = {}
if 'generation_complete' not in st.session_state:
    st.session_state.generation_complete = False

# Define the questions
QUESTIONS = [
    "What is the dataset you want to generate (in simple terms)?",
    "How many rows do you want to generate?",
    "Is the target variable categorical, integer or floating point?",
    "About how many categorical features do you want?",
    "About how many numeric features do you want?",
    "What percentage of the feature values are missing?",
    "What percentage of the feature values are outliers?",
    "Should there be appropriate correlations between features?",
    "How much noise should be added as a percentage (in calculating the target variable)?"
]

# Sidebar for dataset management
st.sidebar.header("Dataset Management")

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

st.sidebar.divider()

# Documentation download button
doc_path = Path("./documentation.pdf")
if doc_path.exists():
    with open(doc_path, "rb") as f:
        st.sidebar.download_button(
            label="📖 Download Documentation",
            data=f.read(),
            file_name="documentation.pdf",
            mime="application/pdf",
            use_container_width=True
        )
else:
    st.sidebar.warning("⚠️ Documentation PDF not found")

# Create tabs
tab1, tab2 = st.tabs(["💬 Chat Assistant", "⚙️ JSON Editor"])

# Tab 1: Chat Interface
with tab1:
    st.header("Dataset Creation Assistant")
    st.markdown("Answer the questions below to generate a dataset configuration.")
    
    # Display chat history
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
    
    # Initialize chat if empty
    if len(st.session_state.chat_messages) == 0:
        st.session_state.chat_messages.append({
            "role": "assistant",
            "content": "Hello! I'll help you create a dataset configuration. Let's start:\n\n" + QUESTIONS[0]
        })
        with chat_container:
            with st.chat_message("assistant"):
                st.markdown(st.session_state.chat_messages[0]["content"])
    
    # Chat input
    if st.session_state.current_question < len(QUESTIONS):
        user_input = st.chat_input("Your answer...")
        
        if user_input:
            # Add user message
            st.session_state.chat_messages.append({
                "role": "user",
                "content": user_input
            })
            
            # Store answer
            st.session_state.chat_answers[st.session_state.current_question] = user_input
            st.session_state.current_question += 1
            
            # Check if all questions answered
            if st.session_state.current_question < len(QUESTIONS):
                # Ask next question
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": QUESTIONS[st.session_state.current_question]
                })
            else:
                # All questions answered - generate dataset
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": "OK. I'm generating your dataset definition..."
                })
            
            st.rerun()
    
    # Generate dataset configuration if all questions answered
    if st.session_state.current_question >= len(QUESTIONS) and len(st.session_state.chat_messages) > 0:
        last_msg = st.session_state.chat_messages[-1]
        if last_msg["role"] == "assistant" and "generating" in last_msg["content"].lower() and not st.session_state.generation_complete:
            with st.spinner("Generating configuration..."):
                # Create chat log
                chat_log = "\n".join([
                    f"{msg['role'].upper()}: {msg['content']}"
                    for msg in st.session_state.chat_messages
                ])
                
                # Load documentation
                documentation = load_documentation()
                
                # Generate JSON
                try:
                    json_config = generate_dataset_from_chat(chat_log, documentation)
                    
                    if json_config:
                        st.session_state.config_text = json_config
                        st.session_state.generation_complete = True
                        st.session_state.chat_messages.append({
                            "role": "assistant",
                            "content": "✅ Dataset configuration generated! You can view and edit it in the JSON Editor tab."
                        })
                        st.rerun()
                    else:
                        st.session_state.chat_messages.append({
                            "role": "assistant",
                            "content": "❌ Failed to generate configuration. Please try again or use the JSON Editor tab."
                        })
                        st.rerun()
                except Exception as e:
                    st.session_state.chat_messages.append({
                        "role": "assistant",
                        "content": f"❌ Error generating configuration: {str(e)}"
                    })
                    st.rerun()
    
    # Action buttons
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🔄 Start Over", use_container_width=True):
            st.session_state.chat_messages = []
            st.session_state.current_question = 0
            st.session_state.chat_answers = {}
            st.session_state.generation_complete = False
            st.rerun()
    
    with col2:
        if st.button("🔁 Regenerate", use_container_width=True, disabled=not st.session_state.generation_complete):
            # Remove the last two assistant messages (success message and generating message)
            if st.session_state.generation_complete:
                # Keep only the questions and answers
                st.session_state.chat_messages = [
                    msg for msg in st.session_state.chat_messages 
                    if not (msg["role"] == "assistant" and ("generating" in msg["content"].lower() or "✅" in msg["content"] or "❌" in msg["content"]))
                ]
                
                # Add generating message back
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": "OK. I'm generating your dataset definition..."
                })
                
                st.session_state.generation_complete = False
                st.rerun()

# Tab 2: JSON Editor
with tab2:
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
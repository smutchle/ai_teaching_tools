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
from AnthropicChatBot import AnthropicChatBot
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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
    """Load documentation content from api_documentation.qmd file."""
    doc_path = Path("./api_documentation.qmd")
    if doc_path.exists():
        with open(doc_path, 'r') as f:
            return f.read()
    return ""

def get_chatbot(llm_provider, api_key_override=None):
    """Create and return a chatbot instance based on the selected provider."""
    if llm_provider == "Claude":
        api_key = api_key_override if api_key_override else os.getenv("CLAUDE_API_KEY")
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        return AnthropicChatBot(
            api_key=api_key,
            model=model,
            use_chat_history=False,
            temperature=0.0
        )
    else:  # Ollama (default)
        model = os.getenv("OLLAMA_MODEL", "gemma3:27b")
        end_point_url = os.getenv("OLLAMA_END_POINT", "http://localhost:11434")
        return OllamaChatBot(
            model=model,
            end_point_url=end_point_url,
            temperature=0.0,
            keep_history=False
        )

def generate_dataset_from_chat(chat_log, documentation, llm_provider="Ollama", api_key_override=None):
    """Generate JSON configuration from chat conversation using LLM."""
    chatbot = get_chatbot(llm_provider, api_key_override)

    prompt = f"""You are a dataset configuration generator. Based on the user's requirements and the documentation provided, generate a valid JSON configuration for a synthetic dataset.

DOCUMENTATION:
{documentation}

USER CONVERSATION:
{chat_log}

CRITICAL JSON STRUCTURE REQUIREMENTS:
1. The JSON MUST start with a "dataset_config" wrapper object
2. Use "n_rows" (not "rows") for the number of rows
3. Include "random_seed" field (default to 42)
4. Use "noise_percent" (not "noise") for target noise as a percentage (e.g., 1.0 for 1%)
5. Feature distributions must be nested objects with "type" field: {{"distribution": {{"type": "normal", "mean": 0, "std": 1}}}}
6. Include "correlations" array (can be empty [])
7. ALL distribution parameters must be inside the distribution object (min, max, mean, std, step_size, etc.)

CRITICAL REQUIREMENTS:
1. Categorical features must use one of these distribution types: uniform, normal, weibull, random_walk, or sequential
2. If the target is categorical, it MUST have exactly 10 category labels in the "categories" array
3. Target expressions can ONLY use NUMERIC features (float or int), NOT categorical features
4. For categorical targets, repeat category labels to create desired class distributions (e.g., ["No", "No", "No", "No", "No", "No", "No", "Yes", "Yes", "Yes"] for 30% "Yes")
5. For time series with seasonality: Create a "seasonality_multipliers" array in the target with length equal to the periodicity (e.g., 12 values for monthly data). Create realistic seasonal patterns (e.g., retail higher in Nov/Dec, energy higher in summer/winter).
6. For time series with multiple seasonal patterns: You can optionally add a "secondary_seasonality_multipliers" array to capture a second seasonal pattern (e.g., day-of-week pattern in addition to monthly pattern).
7. Features can have a "lags" array like [1, 2, 3] which will auto-generate lagged versions (e.g., price_lag1, price_lag2) that can be used in the target expression.
8. If you reference a lagged variable in the expression, you must have created the lag in the feature definition.

INSTRUCTIONS:
1. Generate a complete, valid JSON configuration that matches the schema described in the documentation
2. Use the user's answers to determine:
   - Dataset name and description
   - Time series or cross-sectional
   - Number of rows (use "n_rows" field)
   - Target variable data type (categorical, int, or float)
   - Number of categorical features
   - Number of numeric features (int and float)
   - Missing rates for features
   - Outlier rates for features
   - Correlations between features (if requested)
   - Seasonality (if time series with periodicity specified)
   - Secondary seasonality (if time series with secondary periodicity specified)
   - Noise percentage for target calculation (use "noise_percent" field)
3. Create meaningful feature names and distributions appropriate for the dataset domain
4. For categorical features, use normal/uniform distributions, then add exactly 10 labels in the "categories" array
5. Ensure the target expression ONLY references numeric features (never categorical ones)
6. If target is categorical, provide exactly 10 category labels
7. If seasonal time series, create realistic seasonality_multipliers array based on the domain (length = periodicity)
8. If secondary seasonal time series, create realistic secondary_seasonality_multipliers array (length = secondary periodicity)
9. For features with lags, the lagged versions will be auto-generated and can be used in expressions
10. Return ONLY the JSON configuration starting with {{"dataset_config": {{...}}}}, no additional text or explanation

CORRECT JSON STRUCTURE EXAMPLE:
{{
  "dataset_config": {{
    "name": "example",
    "description": "Example dataset",
    "random_seed": 42,
    "n_rows": 1000,
    "correlations": [],
    "features": [
      {{
        "name": "feature1",
        "data_type": "float",
        "distribution": {{
          "type": "normal",
          "mean": 0,
          "std": 1
        }},
        "missing_rate": 0.1,
        "outlier_rate": 0.05
      }}
    ],
    "target": {{
      "name": "target",
      "data_type": "float",
      "expression": "feature1 * 2",
      "noise_percent": 5.0
    }}
  }}
}}

Generate the JSON configuration now:"""

    response = chatbot.completeAsJSON(prompt)
    return response

def generate_dataset_description(config_text, llm_provider="Ollama", api_key_override=None):
    """Generate a markdown description of the dataset using LLM."""
    chatbot = get_chatbot(llm_provider, api_key_override)

    # Adjust temperature for description generation
    if llm_provider == "Claude":
        chatbot.temperature = 0.3
    else:
        chatbot.temperature = 0.3

    prompt = f"""You are a data science documentation writer. Given a dataset configuration in JSON format, write a clear, concise markdown description of the dataset.

DATASET CONFIGURATION:
{config_text}

INSTRUCTIONS:
1. Write a markdown document that includes:
   - A brief overview of the dataset's purpose and use case
   - Description of each feature (what it represents, type, distribution characteristics)
   - Description of the target variable and how it relates to the features
   - Any special characteristics (correlations, seasonality, time series aspects, etc.)
2. Use clear, professional language suitable for data science documentation
3. Format as markdown with headers, bullet points, and appropriate formatting
4. Do NOT include summary statistics or actual data values
5. Focus on explaining WHAT the dataset contains and WHY it would be useful
6. Return ONLY the markdown text, no JSON or other formatting

Generate the dataset description now:"""

    response = chatbot.complete(prompt)
    return response

def fix_json_with_llm(broken_json, error_message, documentation, llm_provider="Ollama", api_key_override=None):
    """Use LLM to fix broken JSON configuration based on error message."""
    chatbot = get_chatbot(llm_provider, api_key_override)

    prompt = f"""You are a JSON repair expert for dataset configurations. Your task is to fix the broken JSON configuration based on the error message provided.

DOCUMENTATION:
{documentation}

BROKEN JSON:
{broken_json}

ERROR MESSAGE:
{error_message}

CRITICAL REQUIREMENTS:
1. Categorical features must use one of these distribution types: uniform, normal, weibull, random_walk, or sequential
2. If the target is categorical, it MUST have exactly 10 category labels in the "categories" array
3. Target expressions can ONLY use NUMERIC features (float or int), NOT categorical features
4. For categorical targets, repeat category labels to create desired class distributions
5. For time series with seasonality: Create a "seasonality_multipliers" array in the target with length equal to the periodicity
6. For time series with multiple seasonal patterns: You can optionally add a "secondary_seasonality_multipliers" array to capture a second seasonal pattern
7. Features can have a "lags" array like [1, 2, 3] which will auto-generate lagged versions
8. If you reference a lagged variable in the expression, you must have created the lag in the feature definition

INSTRUCTIONS:
1. Analyze the error message to understand what's wrong
2. Fix ONLY the specific issues mentioned in the error
3. Preserve as much of the original configuration as possible
4. Ensure the output is valid JSON that matches the schema
5. If the error is a JSON syntax error, fix the syntax
6. If the error is a validation error, fix the configuration to match requirements
7. Return ONLY the fixed JSON configuration, no additional text or explanation

Generate the fixed JSON configuration now:"""

    response = chatbot.completeAsJSON(prompt)
    return response

# Streamlit UI
st.set_page_config(page_title="Dataset Generator", layout="wide")
st.title("📊 Dataset Generator for Instruction")

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
if 'dataset_description' not in st.session_state:
    st.session_state.dataset_description = ""
if 'llm_provider' not in st.session_state:
    st.session_state.llm_provider = "Claude"
if 'claude_api_key_override' not in st.session_state:
    st.session_state.claude_api_key_override = ""
if 'last_error_message' not in st.session_state:
    st.session_state.last_error_message = None
if 'fixing_json' not in st.session_state:
    st.session_state.fixing_json = False
if 'save_name_preview' not in st.session_state:
    st.session_state.save_name_preview = ""

# Define the questions
QUESTIONS = [
    "What is the dataset you want to generate (in simple terms).  For example, `monthly unit sales for a heavy equipment manufacturer` or `customer churn` or even `I don't know.  You pick a random industry data example` ?",
    "Is the dataset time series or cross-sectional (tabular)?",
    "How many rows do you want to generate?",
    "Is the target variable categorical, int or float?",
    "About how many categorical *features* do you want?",
    "About how many numeric *features* do you want?",
    "What fraction of the *feature* values are missing (e.g. 0.1 = 10%)?",
    "What fraction of the *feature* values are outliers (e.g. 0.1 = 10%)?",
    "Should there be logical correlations between features (e.g. GDP and domestic exports might be correlated)?",
    "If the data is time series, what is the periodicity (you can use words like `daily` or numbers like `24` or `None`)?",
    "If the data is time series, does it have a secondary seasonality pattern? (you can use words like `daily` or numbers like `24` or `None`) ",
    "How much noise should be added as a fraction (e.g. 0.1 = 10%)?",
    "Any other general directions?"
]

# Sidebar for dataset management
st.sidebar.header("Dataset Management")

# LLM Provider Selection
st.sidebar.subheader("🤖 LLM Provider")
llm_provider = st.sidebar.selectbox(
    "Select LLM Provider",
    ["Claude", "Ollama"],
    index=0 if st.session_state.llm_provider == "Claude" else 1,
    key="llm_provider_selector"
)

# Update session state if provider changes
if llm_provider != st.session_state.llm_provider:
    st.session_state.llm_provider = llm_provider
    # Clear description when provider changes
    st.session_state.dataset_description = ""

# Claude API Key Override (only show if Claude is selected)
if llm_provider == "Claude":
    api_key_placeholder = os.getenv("CLAUDE_API_KEY", "")
    if api_key_placeholder:
        api_key_placeholder = f"{api_key_placeholder[:10]}...{api_key_placeholder[-4:]}"

    claude_api_key = st.sidebar.text_input(
        "Claude API Key (optional override)",
        value=st.session_state.claude_api_key_override,
        type="password",
        placeholder=api_key_placeholder if api_key_placeholder else "Enter API key",
        help="Leave blank to use the API key from .env file"
    )

    if claude_api_key != st.session_state.claude_api_key_override:
        st.session_state.claude_api_key_override = claude_api_key

st.sidebar.divider()

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

    # Also load the .qmd description if it exists
    qmd_filepath = DATASETS_DIR / f"{selected}.qmd"
    if qmd_filepath.exists():
        with open(qmd_filepath, 'r') as f:
            st.session_state.dataset_description = f.read()
    else:
        st.session_state.dataset_description = ""

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
        auto_name = config['dataset_config']['name']
        st.session_state.save_name_preview = auto_name
        st.rerun()
    except json.JSONDecodeError as e:
        error_msg = f"JSON syntax error: {str(e)}"
        st.session_state.last_error_message = error_msg
        st.sidebar.error(f"❌ Invalid JSON: {str(e)}")
    except Exception as e:
        error_msg = f"Save error: {str(e)}"
        st.session_state.last_error_message = error_msg
        st.sidebar.error(f"❌ Error: {str(e)}")

# Show dataset name input if save was clicked
if st.session_state.save_name_preview:
    st.sidebar.divider()
    st.sidebar.subheader("💾 Save Dataset")

    # Text input for dataset name
    save_name = st.sidebar.text_input(
        "Dataset Name",
        value=st.session_state.save_name_preview,
        key="save_name_input",
        help="Edit the dataset name if needed, then click Confirm Save"
    )

    col_save1, col_save2 = st.sidebar.columns(2)

    with col_save1:
        if st.button("✅ Confirm Save", use_container_width=True, type="primary"):
            try:
                config = json.loads(st.session_state.config_text)
                # Update the name in the config
                config['dataset_config']['name'] = save_name
                st.session_state.config_text = json.dumps(config, indent=2)
                # Save with the (possibly edited) name
                save_dataset_config(save_name, config)

                # Also save the dataset description as .qmd file if it exists
                if st.session_state.dataset_description:
                    qmd_filepath = DATASETS_DIR / f"{save_name}.qmd"
                    with open(qmd_filepath, 'w') as f:
                        f.write(st.session_state.dataset_description)
                    st.sidebar.success(f"✅ Saved '{save_name}.json' and '{save_name}.qmd'")
                else:
                    st.sidebar.success(f"✅ Saved '{save_name}.json'")

                st.session_state.selected_dataset = save_name
                st.session_state.save_name_preview = ""  # Clear the preview
                st.session_state.last_error_message = None  # Clear error on success
                st.rerun()
            except Exception as e:
                error_msg = f"Save error: {str(e)}"
                st.session_state.last_error_message = error_msg
                st.sidebar.error(f"❌ Error: {str(e)}")

    with col_save2:
        if st.button("❌ Cancel", use_container_width=True):
            st.session_state.save_name_preview = ""  # Clear the preview
            st.rerun()

if col3.button("🗑️ Delete", use_container_width=True, disabled=not st.session_state.selected_dataset):
    if st.session_state.selected_dataset:
        delete_dataset_config(st.session_state.selected_dataset)

        # Also delete the .qmd file if it exists
        qmd_filepath = DATASETS_DIR / f"{st.session_state.selected_dataset}.qmd"
        if qmd_filepath.exists():
            qmd_filepath.unlink()

        st.sidebar.success(f"🗑️ Deleted '{st.session_state.selected_dataset}'")
        st.session_state.selected_dataset = None
        st.session_state.config_text = ""
        st.session_state.dataset_description = ""
        st.rerun()

st.sidebar.divider()

# Generate CSV button
if st.sidebar.button("🎲 Generate CSV", type="primary", use_container_width=True):
    try:
        config = json.loads(st.session_state.config_text)
        dataset = Dataset(config)
        csv_path = generate_csv(dataset)
        st.sidebar.success(f"✅ Generated: {csv_path}")
        st.session_state.last_error_message = None  # Clear error on success

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
        error_msg = f"JSON syntax error: {str(e)}"
        st.session_state.last_error_message = error_msg
        st.sidebar.error(f"❌ Invalid JSON: {str(e)}")
    except Exception as e:
        error_msg = f"Generation error: {str(e)}"
        st.session_state.last_error_message = error_msg
        st.sidebar.error(f"❌ Generation Error: {str(e)}")

st.sidebar.divider()

# Fix Error with AI button (only show if there's an error)
if st.session_state.last_error_message:
    if st.sidebar.button("🤖 Fix Error with AI", type="secondary", use_container_width=True):
        st.session_state.fixing_json = True
        st.rerun()

st.sidebar.divider()

# User documentation download button
user_doc_path = Path("./user_documentation.pdf")
if user_doc_path.exists():
    with open(user_doc_path, "rb") as f:
        st.sidebar.download_button(
            label="📖 Download User Documentation",
            data=f.read(),
            file_name="user_documentation.pdf",
            mime="application/pdf",
            use_container_width=True
        )
else:
    st.sidebar.warning("⚠️ User Documentation PDF not found")

# JSON documentation download button
api_doc_path = Path("./api_documentation.pdf")
if api_doc_path.exists():
    with open(api_doc_path, "rb") as f:
        st.sidebar.download_button(
            label="📚 Download JSON Documentation",
            data=f.read(),
            file_name="api_documentation.pdf",
            mime="application/pdf",
            use_container_width=True
        )
else:
    st.sidebar.warning("⚠️ JSON Documentation PDF not found")

# Create tabs
tab1, tab2, tab3 = st.tabs(["💬 Chat Assistant", "⚙️ JSON Editor", "📄 Dataset Description"])

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
                    # Get API key override if using Claude
                    api_key_override = st.session_state.claude_api_key_override if st.session_state.llm_provider == "Claude" else None

                    json_config = generate_dataset_from_chat(
                        chat_log,
                        documentation,
                        st.session_state.llm_provider,
                        api_key_override
                    )

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
        # Clear description when config changes
        st.session_state.dataset_description = ""
    
    # Validation area
    st.header("Configuration Validation")
    if st.session_state.config_text:
        try:
            config = json.loads(st.session_state.config_text)
            dataset = Dataset(config)
            validation_results = dataset.validate()

            if validation_results['valid']:
                st.success("✅ Configuration is valid!")
                st.session_state.last_error_message = None  # Clear error on success

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
                    if 'seasonality_multipliers' in dc.get('target', {}):
                        st.write(f"**Seasonality:** Yes (period: {len(dc['target']['seasonality_multipliers'])})")
            else:
                error_msg = "Configuration validation errors:\n" + "\n".join([f"  • {error}" for error in validation_results['errors']])
                st.session_state.last_error_message = error_msg

                st.error("❌ Configuration has errors:")
                for error in validation_results['errors']:
                    st.error(f"  • {error}")

                # Add "Fix Error with AI" button
                if st.button("🤖 Fix Error with AI", type="primary", key="fix_validation_error"):
                    st.session_state.fixing_json = True
                    st.rerun()

        except json.JSONDecodeError as e:
            error_msg = f"JSON syntax error: {str(e)}"
            st.session_state.last_error_message = error_msg
            st.warning(f"⚠️ Invalid JSON syntax: {str(e)}")

            # Add "Fix Error with AI" button for JSON errors
            if st.button("🤖 Fix Error with AI", type="primary", key="fix_json_error"):
                st.session_state.fixing_json = True
                st.rerun()

        except Exception as e:
            error_msg = f"Validation error: {str(e)}"
            st.session_state.last_error_message = error_msg
            st.error(f"❌ Validation error: {str(e)}")

            # Add "Fix Error with AI" button for other errors
            if st.button("🤖 Fix Error with AI", type="primary", key="fix_other_error"):
                st.session_state.fixing_json = True
                st.rerun()

    # Handle AI-based JSON fixing
    if st.session_state.fixing_json and st.session_state.last_error_message:
        with st.spinner("🤖 Using AI to fix the JSON configuration..."):
            try:
                # Load documentation
                documentation = load_documentation()

                # Get API key override if using Claude
                api_key_override = st.session_state.claude_api_key_override if st.session_state.llm_provider == "Claude" else None

                # Fix the JSON
                fixed_json = fix_json_with_llm(
                    st.session_state.config_text,
                    st.session_state.last_error_message,
                    documentation,
                    st.session_state.llm_provider,
                    api_key_override
                )

                if fixed_json:
                    st.session_state.config_text = fixed_json
                    st.session_state.fixing_json = False
                    st.session_state.last_error_message = None
                    st.success("✅ JSON fixed! Please review the changes.")
                    st.rerun()
                else:
                    st.error("❌ Failed to fix JSON. Please try manual editing.")
                    st.session_state.fixing_json = False
            except Exception as e:
                st.error(f"❌ Error during AI fix: {str(e)}")
                st.session_state.fixing_json = False

# Tab 3: Dataset Description
with tab3:
    st.header("Dataset Description")
    
    if not st.session_state.config_text:
        st.info("💡 Generate or load a dataset configuration first to view its description.")
    else:
        # Check if description needs to be generated
        if not st.session_state.dataset_description:
            with st.spinner("Generating dataset description..."):
                try:
                    # Get API key override if using Claude
                    api_key_override = st.session_state.claude_api_key_override if st.session_state.llm_provider == "Claude" else None

                    description = generate_dataset_description(
                        st.session_state.config_text,
                        st.session_state.llm_provider,
                        api_key_override
                    )
                    st.session_state.dataset_description = description
                except Exception as e:
                    st.error(f"❌ Error generating description: {str(e)}")
        
        # Display the description
        if st.session_state.dataset_description:
            st.markdown(st.session_state.dataset_description)
            
            # Download button for description
            st.download_button(
                label="⬇️ Download Description (Markdown)",
                data=st.session_state.dataset_description,
                file_name="dataset_description.md",
                mime="text/markdown"
            )
            
            # Regenerate button
            if st.button("🔁 Regenerate Description"):
                st.session_state.dataset_description = ""
                st.rerun()
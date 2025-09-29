import streamlit as st
import pandas as pd
import json
import os
import shutil
from dotenv import load_dotenv
import re
import base64

# Import necessary chatbot classes
from OllamaChatBot import OllamaChatBot
from AnthropicChatBot import AnthropicChatBot
from OpenAIChatBot import OpenAIChatBot
from GoogleChatBot import GoogleChatBot # Added Google Chatbot import

import nbformat
from nbformat import v4, writes
from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell

# Load environment variables from .env file in the same folder
load_dotenv()

# --- Configuration Loading ---
# Load API keys and endpoints from environment variables
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") # Added Google API Key
OLLAMA_END_POINT = os.getenv("OLLAMA_END_POINT", "http://localhost:11434") # Default if not set

# Load default models and parse them into lists
def parse_models(env_var):
    models = os.getenv(env_var)
    if models:
        return [model.strip() for model in models.split(',')]
    return []

ANTHROPIC_MODELS = parse_models("ANTHROPIC_MODEL")
OPENAI_MODELS = parse_models("OPENAI_MODEL")
GOOGLE_MODELS = parse_models("GOOGLE_MODEL") # Added Google Models
OLLAMA_MODELS = parse_models("OLLAMA_MODEL")

# Store configs in a dictionary for easier access
PROVIDER_CONFIG = {
    "Anthropic": {"api_key": ANTHROPIC_API_KEY, "models": ANTHROPIC_MODELS},
    "OpenAI": {"api_key": OPENAI_API_KEY, "models": OPENAI_MODELS},
    "Google": {"api_key": GOOGLE_API_KEY, "models": GOOGLE_MODELS}, # Added Google config
    "Ollama": {"endpoint": OLLAMA_END_POINT, "models": OLLAMA_MODELS},
}

# --- Streamlit Page Setup ---
st.set_page_config(layout="wide", initial_sidebar_state="collapsed")

# Custom CSS
st.markdown(
    """
<style>
    .reportview-container .main .block-container {
        max-width: 1280px;
        padding-top: 2rem;
        padding-right: 2rem;
        padding-left: 2rem;
        padding-bottom: 2rem;
    }
    [title="Show password text"] { /* Hide the eye icon for password */
        display: none;
    }
</style>
""",
    unsafe_allow_html=True,
)

# --- Helper Functions ---

def upload_lectures():
    uploaded_file = st.file_uploader("Upload Lectures CSV", type="csv")
    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
            required_columns = {"title", "description"}
            if not all(col in df.columns for col in required_columns):
                st.error("CSV must contain 'title' and 'description' columns")
                return

            df["selected"] = False
            st.session_state.lecture_df = df
            st.success("Lectures uploaded successfully!")
        except Exception as e:
            st.error(f"Error uploading file: {str(e)}")

def upload_topics():
    uploaded_file = st.file_uploader("Upload Topics CSV", type="csv")
    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
            required_columns = {"lecture_title", "topic_title", "topic_description"}
            if not all(col in df.columns for col in required_columns):
                st.error(
                    "CSV must contain 'lecture_title', 'topic_title', and 'topic_description' columns"
                )
                return

            df["selected"] = False
            st.session_state.topics_df = df
            st.success("Topics uploaded successfully!")
        except Exception as e:
            st.error(f"Error uploading file: {str(e)}")

def select_all_lectures():
    if "lecture_df" in st.session_state:
        st.session_state.lecture_df["selected"] = True

def select_none_lectures():
    if "lecture_df" in st.session_state:
        st.session_state.lecture_df["selected"] = False

def select_all_topics():
    if "topics_df" in st.session_state:
        st.session_state.topics_df["selected"] = True

def select_none_topics():
    if "topics_df" in st.session_state:
        st.session_state.topics_df["selected"] = False

def get_table_download_link(df, filename, text):
    csv = df.to_csv(index=False)
    b64 = base64.b64encode(csv.encode()).decode()
    href = f'<a href="data:file/csv;base64,{b64}" download="{filename}">{text}</a>'
    return href

def display_notebook(notebook_path):
    try:
        with open(notebook_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Determine language based on extension
        if notebook_path.endswith(".ipynb"):
            st.code(content, language="json")
        elif notebook_path.endswith(".qmd"):
            st.markdown(f"```markdown\n{content}\n```") # Display Quarto as markdown block
        else:
            st.text(content)
    except FileNotFoundError:
        st.error(f"Error: File not found at {notebook_path}")
    except Exception as e:
        st.error(f"Error reading notebook: {e}")


# Modified function to create chatbot based on selected type and loaded config
def create_chatbot(chatbot_type, model, api_key_override=None, endpoint_override=None):
    """
    Creates a chatbot instance based on the selected type.
    Uses API keys/endpoints from .env by default, but allows overrides from UI.
    """
    config = PROVIDER_CONFIG.get(chatbot_type)
    if not config:
        st.error(f"Invalid chatbot type specified: {chatbot_type}")
        return None

    try:
        if chatbot_type == "Anthropic":
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("Anthropic API Key not found in .env or provided in UI.")
                return None
            return AnthropicChatBot(key, model)
        elif chatbot_type == "OpenAI":
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("OpenAI API Key not found in .env or provided in UI.")
                return None
            return OpenAIChatBot(key, model)
        elif chatbot_type == "Google": # Added Google case
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("Google API Key not found in .env or provided in UI.")
                return None
            return GoogleChatBot(key, model)
        elif chatbot_type == "Ollama":
            endpoint = endpoint_override if endpoint_override else config.get("endpoint")
            if not endpoint:
                st.error("Ollama Endpoint not found in .env or provided in UI.")
                return None
            # Ollama doesn't typically use an API key in the same way
            return OllamaChatBot(model, endpoint)
        else:
            st.error(f"Unknown ChatBot type: {chatbot_type}")
            return None
    except Exception as e:
        st.error(f"Error initializing {chatbot_type} chatbot for model {model}: {e}")
        return None


def add_new_lecture():
    with st.form(key="add_lecture_form"):
        new_title = st.text_input("New Lecture Title")
        new_description = st.text_area("New Lecture Description")
        submit_button = st.form_submit_button(label="Add New Lecture")

    if submit_button:
        if not new_title or not new_description:
            st.warning("Please provide both a title and description for the new lecture.")
            return
        new_lecture = {
            "title": new_title,
            "description": new_description,
            "selected": True, # Default to selected
        }
        if "lecture_df" not in st.session_state or st.session_state.lecture_df is None:
            st.session_state.lecture_df = pd.DataFrame([new_lecture])
        else:
            st.session_state.lecture_df = pd.concat(
                [st.session_state.lecture_df, pd.DataFrame([new_lecture])],
                ignore_index=True,
            )
        st.success(f"Lecture '{new_title}' added.")
        st.rerun()


def add_new_topic():
    with st.form(key="add_topic_form"):
        if "lecture_df" in st.session_state and not st.session_state.lecture_df.empty:
            lectures = st.session_state.lecture_df["title"].unique()
            if len(lectures) > 0:
                new_lecture = st.selectbox("Select Lecture", lectures)
            else:
                st.warning("No lectures available to add topics to. Please add or generate lectures first.")
                new_lecture = None # Indicate no lecture available
        else:
            st.warning("No lectures available. Please add or generate lectures first.")
            new_lecture = None # Indicate no lecture available

        new_title = st.text_input("New Topic Title")
        new_description = st.text_area("New Topic Description")
        submit_button = st.form_submit_button(label="Add New Topic", disabled=(new_lecture is None))

    if submit_button and new_lecture:
        if not new_title or not new_description:
            st.warning("Please provide both a title and description for the new topic.")
            return
        new_topic = {
            "lecture_title": new_lecture,
            "topic_title": new_title,
            "topic_description": new_description,
            "selected": True, # Default to selected
        }
        if "topics_df" not in st.session_state or st.session_state.topics_df is None:
            st.session_state.topics_df = pd.DataFrame([new_topic])
        else:
            st.session_state.topics_df = pd.concat(
                [st.session_state.topics_df, pd.DataFrame([new_topic])], ignore_index=True
            )
        st.success(f"Topic '{new_title}' added to lecture '{new_lecture}'.")
        st.rerun()


def generate_lectures(
    chatbot,
    course_title,
    course_description,
    num_lectures,
    lecture_length,
    level="graduate",
):
    if not chatbot: return None # Guard clause
    prompt = f"""
    Create a list of {num_lectures} lectures for a {level}-level course titled "{course_title}".
    Course description: {course_description}
    Each lecture is approximately {lecture_length} minutes long.
    For each lecture, provide a unique title and a brief description (20-100 words).
    The output must be pure JSON, containing only a valid JSON array of objects. Do not include any introductory text, explanations, or code block markers like ```json ... ```.
    Format the output as a JSON array of objects, where each object has exactly two keys: 'title' (string) and 'description' (string).

    Example of the exact required output format:
    [
      {{"title": "Lecture 1: Introduction", "description": "Overview of the course topics and goals."}},
      {{"title": "Lecture 2: Core Concepts", "description": "Exploring fundamental principles and definitions."}}
    ]
    """
    try:
        response = chatbot.completeAsJSON(prompt) # Use the JSON specific method if available
        # Attempt to parse the response directly as JSON
        parsed_response = json.loads(response)

        # Validate the structure
        if not isinstance(parsed_response, list):
            raise ValueError("Expected a JSON array as the root object.")

        if not all(
            isinstance(lecture, dict) and
            "title" in lecture and isinstance(lecture["title"], str) and
            "description" in lecture and isinstance(lecture["description"], str) and
            len(lecture.keys()) == 2 # Ensure exactly two keys
            for lecture in parsed_response
        ):
            raise ValueError(
                "Each element in the array must be an object with exactly 'title' (string) and 'description' (string) keys."
            )

        return parsed_response

    except json.JSONDecodeError as e:
        st.error(f"Error decoding JSON response from LLM: {e}")
        st.text("Raw response received:")
        st.code(response, language='text')
        return None
    except ValueError as e:
        st.error(f"Invalid response format: {e}")
        st.text("Raw response received:")
        st.code(response, language='text')
        return None
    except Exception as e:
        st.error(f"An unexpected error occurred during lecture generation: {e}")
        st.text("Raw response received (if available):")
        st.code(response if 'response' in locals() else "Response not captured.", language='text')
        return None


def generate_topics(chatbot, lectures, min_topics, max_topics):
    if not chatbot: return [] # Guard clause

    all_lecture_topics = []
    total_lectures = len(lectures)

    progress_bar = st.progress(0)
    status_text = st.empty()

    for i, lecture in enumerate(lectures):
        status_text.text(f"Generating topics for lecture {i+1}/{total_lectures}: {lecture['title']}...")
        prompt = f"""
        Generate a list of {min_topics} to {max_topics} key topics for the following lecture:
        Lecture Title: {lecture['title']}
        Lecture Description: {lecture['description']}

        For each topic, provide a unique title and a brief description (20-100 words).
        The output must be pure JSON, containing only a valid JSON array of objects. Do not include any introductory text, explanations, or code block markers like ```json ... ```.
        Format the output as a JSON array of objects, where each object has exactly two keys: 'title' (string) and 'description' (string).

        Example of the exact required output format:
        [
          {{"title": "Topic 1.1: Sub-concept A", "description": "Detailed explanation of sub-concept A."}},
          {{"title": "Topic 1.2: Sub-concept B", "description": "Relationship between sub-concept A and B."}}
        ]
        """
        lecture_topics = []
        try:
            response = chatbot.completeAsJSON(prompt) # Use JSON specific method
            parsed_response = json.loads(response)

            # Validate structure
            if not isinstance(parsed_response, list):
                raise ValueError("Expected a JSON array as the root object.")

            if not all(
                isinstance(topic, dict) and
                "title" in topic and isinstance(topic["title"], str) and
                "description" in topic and isinstance(topic["description"], str) and
                len(topic.keys()) == 2 # Ensure exactly two keys
                for topic in parsed_response
            ):
                raise ValueError(
                    "Each element in the array must be an object with exactly 'title' (string) and 'description' (string) keys."
                )

            lecture_topics = parsed_response

        except (json.JSONDecodeError, ValueError) as e:
            st.error(f"Error processing topics for lecture '{lecture['title']}': {e}. Skipping this lecture.")
            st.text("Raw response received:")
            st.code(response if 'response' in locals() else "Response not captured.", language='text')
            lecture_topics = [] # Ensure it's an empty list on error
        except Exception as e:
             st.error(f"An unexpected error occurred generating topics for lecture '{lecture['title']}': {e}. Skipping.")
             st.text("Raw response received (if available):")
             st.code(response if 'response' in locals() else "Response not captured.", language='text')
             lecture_topics = []

        all_lecture_topics.append(lecture_topics)
        progress_bar.progress((i + 1) / total_lectures)

    status_text.text("Topic generation complete.")
    progress_bar.empty() # Remove progress bar after completion
    return all_lecture_topics


def create_notebook(
    chatbot,
    lecture_title,
    topic_title,
    topic_description,
    full_topics_list_json, # Pass the JSON string directly
    instructions_template, # Use the template from the UI
    course_title, # Need course title for formatting
    examples_programming_language,
    notebook_type,
    libraries_used,
):
    if not chatbot: return None # Guard clause

    # Format the instructions with current context
    instructions = instructions_template.format(
        course_title=course_title,
        examples_programming_language=examples_programming_language,
        libraries_used=libraries_used,
        # Add other placeholders if needed in the template
    )

    lib_install_req = ""
    if examples_programming_language == "Python":
         lib_install_req = "%pip install -q <library-name>" # Placeholder, actual libs might vary
    elif examples_programming_language == "R":
         lib_install_req = "install.packages(\"<library-name>\")" # Placeholder

    output_requirements = ""
    if notebook_type == "Jupyter notebook":
        output_requirements = f"""
        Output:
        - Produce a valid Jupyter Notebook (.ipynb) JSON structure directly.
        - The entire response MUST be ONLY the raw JSON content of the .ipynb file, starting with `{{` and ending with `}}`.
        - Do NOT include ```json ... ``` markers or any text before or after the JSON content.
        - Ensure all code cells have the correct language specified (e.g., 'python' or 'R').
        - Embed any small datasets directly or provide clear instructions/links for loading public data.
        - Content should be verbose and detailed, especially in markdown cells explaining concepts.
        - Use clearly labeled visualizations where appropriate.
        - Ensure mathematical equations are correctly formatted using LaTeX within markdown cells (e.g., `$E=mc^2$`).
        - Include code for installing necessary libraries using '{lib_install_req}' if applicable, placed early in the notebook.
        """
        completion_method = chatbot.completeAsJSON # Expecting direct JSON output
    else: # Quarto notebook (.qmd)
        output_requirements = f"""
        Output:
        - Produce a valid Quarto markdown file (.qmd) content.
        - The entire response MUST be ONLY the raw text content of the .qmd file.
        - Do NOT include ```qmd ... ``` or ```markdown ... ``` markers or any text before or after the .qmd content.
        - Start the file appropriately (e.g., with YAML header if needed, though maybe not strictly necessary for basic content).
        - Use standard Markdown syntax for text, headers, lists, etc.
        - Use Quarto code blocks (e.g., ```{{python}} ... ``` or ```{{r}} ... ```) for code.
        - Embed any small datasets directly or provide clear instructions/links for loading public data.
        - Content should be verbose and detailed, explaining concepts clearly.
        - Use clearly labeled visualizations where appropriate.
        - Ensure mathematical equations are correctly formatted using LaTeX (e.g., `$E=mc^2$`).
        - Include code for installing necessary libraries using '{lib_install_req}' if applicable, placed in an appropriate code block near the beginning.
        - Ensure lists in markdown have a blank line before them as requested.
        """
        completion_method = chatbot.complete # Expecting raw text output

    prompt = f"""
    Task: Create a detailed {notebook_type} about a specific topic within a lecture.

    Context:
    Course Title: {course_title}
    Lecture: {lecture_title}
    Topic for this Notebook: {topic_title}
    Topic Description: {topic_description}
    Programming Language for Examples: {examples_programming_language}
    Libraries to Use: {libraries_used}
    Full list of topics in the '{lecture_title}' lecture (for context on neighbors):
    {full_topics_list_json}

    Instructions for Notebook Content:
    {instructions}

    Output Format Requirements:
    {output_requirements}

    Generate the complete {notebook_type} content now based *only* on the instructions and requirements above.
    """

    try:
        response = completion_method(prompt)

        # Post-processing for Quarto to ensure it's not wrapped in markdown fences
        if notebook_type == "Quarto notebook":
             # Use the extraction method if the LLM still wraps it (optional robustness)
             response = chatbot.extract_markdown_content(response, "qmd")
             # Basic check: Does it look like markdown? (Not foolproof)
             if response.startswith("```") and response.endswith("```"):
                 st.warning(f"LLM response for {topic_title} might be incorrectly wrapped in code fences. Attempting to clean.")
                 response = re.sub(r'^```[a-zA-Z]*\n?', '', response)
                 response = re.sub(r'\n?```$', '', response)

        # Post-processing/Validation for Jupyter (optional but recommended)
        if notebook_type == "Jupyter notebook":
            try:
                # Check if it's valid JSON
                nb_data = json.loads(response)
                # Check if it resembles a notebook structure (basic check)
                if not isinstance(nb_data, dict) or "cells" not in nb_data or "metadata" not in nb_data:
                     raise ValueError("Response is valid JSON but doesn't look like a Jupyter notebook.")
                # Attempt to validate using nbformat (stricter check)
                nbformat.validate(nb_data)
            except (json.JSONDecodeError, ValueError, nbformat.ValidationError) as json_val_err:
                 st.error(f"LLM response for {topic_title} was not valid Jupyter Notebook JSON: {json_val_err}")
                 st.text("Raw response received:")
                 st.code(response, language='text')
                 return None # Indicate failure


        return response

    except Exception as e:
        st.error(f"An unexpected error occurred during notebook generation for '{topic_title}': {e}")
        # Log the prompt if debugging is needed
        # print("--- Failing Prompt ---")
        # print(prompt)
        # print("--- End Failing Prompt ---")
        return None


# --- Streamlit App Layout ---
st.title("📚 Curriculum Generator")

# --- Initialize session state variables ---
if "lecture_df" not in st.session_state:
    st.session_state.lecture_df = pd.DataFrame(columns=["title", "description", "selected"])
if "topics_df" not in st.session_state:
    st.session_state.topics_df = pd.DataFrame(columns=["lecture_title", "topic_title", "topic_description", "selected"])
if "selected_provider" not in st.session_state:
    # Default to the first provider with models, or Anthropic if none have models
    first_provider = "Anthropic"
    for p, c in PROVIDER_CONFIG.items():
        if c.get("models"):
            first_provider = p
            break
    st.session_state.selected_provider = first_provider

if "selected_model" not in st.session_state:
    st.session_state.selected_model = None # Will be set based on provider

if "api_key_input" not in st.session_state:
     st.session_state.api_key_input = ""

if "ollama_endpoint_input" not in st.session_state:
     st.session_state.ollama_endpoint_input = PROVIDER_CONFIG["Ollama"]["endpoint"]


tab_settings, tab_course, tab_lectures, tab_topics, tab_notebooks = st.tabs(
    ["⚙️ Settings", "📖 Course", "📖 Lectures", "📝 Topics", "💻 Notebooks"]
)

with tab_settings:
    st.header("⚙️ Settings")

    st.subheader("📁 Output Settings")
    notebook_path = st.text_input("Notebook Output Path", value="./output")
    # use_rag = st.checkbox("Use RAG for Course Continuity?", True) # RAG TBD

    st.subheader("🤖 LLM Provider Settings")

    # --- Dynamic LLM Selection ---
    # 1. Select Provider
    available_providers = list(PROVIDER_CONFIG.keys())
    selected_provider = st.selectbox(
        "Select LLM Provider",
        available_providers,
        index=available_providers.index(st.session_state.selected_provider) # Persist selection
    )
    st.session_state.selected_provider = selected_provider # Update session state

    # 2. Select Model based on Provider
    provider_models = PROVIDER_CONFIG[selected_provider].get("models", [])
    if not provider_models:
        st.warning(f"No models found for {selected_provider} in the .env file. Please add models (e.g., {selected_provider.upper()}_MODEL=model1,model2) to your .env file.")
        selected_model = None
        model_disabled = True
    else:
        # Try to keep the previously selected model if it's valid for the new provider, else default to first
        current_model_index = 0
        if st.session_state.selected_model in provider_models:
            current_model_index = provider_models.index(st.session_state.selected_model)

        selected_model = st.selectbox(
            "Select Model",
            provider_models,
            index=current_model_index,
            help=f"Select the {selected_provider} model to use.",
            key=f"model_selector_{selected_provider}" # Key changes with provider to force reset if needed
        )
        st.session_state.selected_model = selected_model # Update session state
        model_disabled = False


    # 3. API Key / Endpoint Input (Conditionally Display)
    api_key_label = f"{selected_provider} API Key (Optional Override)"
    endpoint_label = f"{selected_provider} Endpoint (Optional Override)"

    # Display relevant input field based on provider
    if selected_provider in ["Anthropic", "OpenAI", "Google"]:
        default_key = PROVIDER_CONFIG[selected_provider].get("api_key", "")
        # Initialize api_key_input specific to provider if not set
        if f"{selected_provider}_api_key_input" not in st.session_state:
             st.session_state[f"{selected_provider}_api_key_input"] = default_key

        api_key_input = st.text_input(
            api_key_label,
            value=st.session_state.get(f"{selected_provider}_api_key_input", default_key), # Get provider-specific state
            type="password",
            help="Leave blank to use the key from .env file, or enter a key to override it.",
            key=f"api_key_input_{selected_provider}" # Unique key per provider
        )
        st.session_state[f"{selected_provider}_api_key_input"] = api_key_input # Store provider-specific state
        st.session_state.api_key_input = api_key_input # Also store in general state for create_chatbot usage
        st.session_state.ollama_endpoint_input = None # Clear endpoint state if switching away from Ollama

    elif selected_provider == "Ollama":
        default_endpoint = PROVIDER_CONFIG[selected_provider].get("endpoint", "http://localhost:11434")
        # Initialize endpoint_input if not set
        if "ollama_endpoint_input" not in st.session_state:
            st.session_state.ollama_endpoint_input = default_endpoint

        ollama_endpoint_input = st.text_input(
            endpoint_label,
            value=st.session_state.ollama_endpoint_input, # Use specific state
            help="Leave blank to use the endpoint from .env file (or default), or enter an endpoint to override it.",
            key="ollama_endpoint_input_widget"
        )
        st.session_state.ollama_endpoint_input = ollama_endpoint_input # Store specific state
        st.session_state.api_key_input = None # Clear API key state if switching to Ollama

# --- Global Access to Settings ---
# These reflect the latest selections in the Settings tab
CHATBOT_TYPE = st.session_state.selected_provider
SELECTED_MODEL = st.session_state.selected_model
API_KEY_OVERRIDE = st.session_state.api_key_input
OLLAMA_ENDPOINT_OVERRIDE = st.session_state.ollama_endpoint_input


with tab_course:
    st.subheader("🎓 Course Settings")
    # Use st.session_state to preserve values across tabs/reruns
    st.session_state.course_title = st.text_input(
        "Course Title",
        value=st.session_state.get('course_title', "Generative AI")
    )
    st.session_state.course_description = st.text_area(
        "Course Description",
        value=st.session_state.get('course_description', "Covering both theoretical design and practical application of generative AI.")
    )
    st.session_state.lecture_level = st.text_input(
        "Lecture Level",
        value=st.session_state.get('lecture_level', "graduate")
    )
    st.session_state.examples_programming_language = st.selectbox(
        "Example Programming Language",
        ["Python", "R"],
        index=["Python", "R"].index(st.session_state.get('examples_programming_language', 'Python'))
    )
    st.session_state.libraries_used = st.text_input(
        "Libraries to Use (Informational)",
        value=st.session_state.get('libraries_used', "Use appropriate libraries like scikit-learn, PyTorch, TensorFlow, tidyverse, etc.")
    )
    st.session_state.notebook_type = st.selectbox(
        "Notebook type",
        ["Quarto notebook", "Jupyter notebook"],
        index=["Quarto notebook", "Jupyter notebook"].index(st.session_state.get('notebook_type', 'Quarto notebook'))
    )

    if st.session_state.notebook_type == "Jupyter notebook":
        extension = "ipynb"
    else:
        extension = "qmd"
    st.session_state.extension = extension # Store for later use

    col1, col2 = st.columns(2)
    with col1:
        st.session_state.num_lectures = st.number_input(
            "Number of Lectures to Generate",
            value=st.session_state.get('num_lectures', 10), min_value=1, max_value=50
        )
    with col2:
        st.session_state.lecture_length = st.number_input(
            "Approx. Lecture Length (minutes)",
            value=st.session_state.get('lecture_length', 60), min_value=10, max_value=180
        )

    st.subheader("📝 Notebook Instructions Template")
    default_instructions = """
Include the following sections:
1.  **Topic Overview**:
    *   Provide a detailed overview, focusing on building intuition.
    *   Conclude with the importance and relevance of this topic within the broader lecture/course ({course_title}).
2.  **Background & Theory**:
    *   This section must be the bulk of the document. It should be comprehensive, detailed and packed with information.  It should take 30-60 minutes to cover this Background & Theory section.
    *   Cover historical context (if applicable) and theoretical foundations.
    *   Include mathematical derivations (as appropriate) using LaTeX (e.g., `$E=mc^2$`). Define all terms clearly. Explain reasoning behind steps.
    *   Use Mermaid diagrams (in appropriate ```{{mermaid}} ... ``` blocks) or other visualizations if they aid understanding.
    *   Link to 1-3 high-quality external resources (papers, articles, tutorials) for further reading if possible. Include DOI links or other URLs (if known).
     *  Add appropriate inline references that are linked to the References section.  Use APA format for references.
3.  **Practical Example / Code Implementation**:
    *   Provide a working code example in {examples_programming_language}.
    *   Use the libraries mentioned ({libraries_used}) or other suitable ones.
    *   Ensure code is well-commented and explains the implementation steps.
    *   If data is needed, either generate synthetic data or use a small, easily accessible public dataset (provide loading code).
4.  **Student Exercise**:
    *   Design a small homework problem related to the topic. It could be conceptual, require modifying the example code, or involve applying the concept to a new scenario.
5.  **Exercise Solution**:
    *   Provide a clear solution or key steps for the student exercise.
6.  **Quiz**:
    *   Create a five-question quiz (multiple-choice or true/false) covering key aspects of the topic.
    *   Make the answers viewable directly after the question when the user clicks to unfold the answer.

General Formatting Notes:
*   Ensure code blocks are correctly specified for {examples_programming_language}. 
*   Be verbose and pedagogical throughout.
*   All code blocks should be folded by default.
*   When creating markdown lists, always have a blank line right before the list starts.

"""
    st.session_state.instructions = st.text_area(
        "Notebook Instructions (can use placeholders like {course_title}, {examples_programming_language}, {libraries_used})",
        value=st.session_state.get('instructions', default_instructions),
        height=450
    )


with tab_lectures:
    st.header("📖 Lectures")
    st.subheader("📤 Upload Lectures (Optional)")
    upload_lectures()

    st.subheader("🤖 Generate Lectures")
    if st.button("✨ Generate Lectures", key="gen_lectures_btn", disabled=(SELECTED_MODEL is None)):
        if not SELECTED_MODEL:
             st.error("Please select a valid model in the Settings tab first.")
        else:
            # Use the centrally managed settings
            chatbot = create_chatbot(CHATBOT_TYPE, SELECTED_MODEL, API_KEY_OVERRIDE, OLLAMA_ENDPOINT_OVERRIDE)
            if chatbot:
                with st.spinner(f"Generating {st.session_state.num_lectures} lectures using {CHATBOT_TYPE} ({SELECTED_MODEL})..."):
                    lectures = generate_lectures(
                        chatbot,
                        st.session_state.course_title,
                        st.session_state.course_description,
                        st.session_state.num_lectures,
                        st.session_state.lecture_length,
                        st.session_state.lecture_level,
                    )
                    if lectures:
                        # Overwrite or initialize lecture_df
                        df = pd.DataFrame(lectures)
                        df["selected"] = True # Default generated lectures to selected
                        st.session_state.lecture_df = df
                        st.success(f"{len(lectures)} Lectures generated successfully!")
                        # Clear existing topics if lectures are regenerated
                        if 'topics_df' in st.session_state:
                            st.session_state.topics_df = pd.DataFrame(columns=st.session_state.topics_df.columns)
                            st.info("Existing topics cleared as lectures were regenerated.")
                        st.rerun() # Rerun to update the display immediately
                    else:
                        # Error messages handled within generate_lectures
                        st.warning("Lecture generation failed. Please check the error messages above and your settings/API key.")
            else:
                 st.error(f"Failed to create chatbot for {CHATBOT_TYPE}. Check settings and API key.")

    # Display and manage lectures
    if "lecture_df" in st.session_state and not st.session_state.lecture_df.empty:
        st.subheader("📋 Manage Lectures")

        col1, col2, col3 = st.columns([1,1,5]) # Add some space
        with col1:
            if st.button("Select All", key="sel_all_lec"):
                select_all_lectures()
                st.rerun()
        with col2:
            if st.button("Select None", key="sel_none_lec"):
                select_none_lectures()
                st.rerun()

        # Make columns editable using st.data_editor
        st.session_state.lecture_df = st.data_editor(
            st.session_state.lecture_df,
            column_config={
                "selected": st.column_config.CheckboxColumn("Select", default=False),
                "title": st.column_config.TextColumn("Lecture Title", width="medium"),
                "description": st.column_config.TextColumn("Description", width="large"),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic", # Allow adding/deleting rows
            key="lecture_editor"
        )

        # Add new lecture functionality (manual add)
        st.subheader("➕ Add New Lecture Manually")
        add_new_lecture()

        # Download link for lectures CSV
        st.markdown(
            get_table_download_link(
                st.session_state.lecture_df, "lectures.csv", "📥 Download Lectures as CSV"
            ),
            unsafe_allow_html=True,
        )
    else:
        st.info("No lectures loaded or generated yet. Use the options above.")


with tab_topics:
    st.header("📝 Topics")
    st.subheader("📤 Upload Topics (Optional)")
    upload_topics()

    st.subheader("⚙️ Topic Generation Settings")
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.min_topics = st.number_input(
            "Min topics per lecture:", min_value=1, max_value=10,
            value=st.session_state.get('min_topics', 1), step=1
        )
    with col2:
        st.session_state.max_topics = st.number_input(
            "Max topics per lecture:", min_value=st.session_state.get('min_topics', 1), max_value=30,
            value=st.session_state.get('max_topics', 8), step=1
        )

    st.subheader("🤖 Generate Topics for Selected Lectures")
    # Check if there are any selected lectures
    lectures_available = "lecture_df" in st.session_state and not st.session_state.lecture_df.empty
    selected_lectures_df = st.session_state.lecture_df[st.session_state.lecture_df["selected"]] if lectures_available else pd.DataFrame()
    disable_gen_topics = selected_lectures_df.empty or (SELECTED_MODEL is None)

    if st.button("✨ Generate Topics", key="gen_topics_btn", disabled=disable_gen_topics):
        if not SELECTED_MODEL:
             st.error("Please select a valid model in the Settings tab first.")
        elif not lectures_available or selected_lectures_df.empty:
            st.warning("Please select at least one lecture in the 'Lectures' tab before generating topics.")
        else:
            selected_lectures_list = selected_lectures_df.to_dict("records")
            chatbot = create_chatbot(CHATBOT_TYPE, SELECTED_MODEL, API_KEY_OVERRIDE, OLLAMA_ENDPOINT_OVERRIDE)
            if chatbot:
                with st.spinner(f"Generating topics for {len(selected_lectures_list)} selected lecture(s) using {CHATBOT_TYPE} ({SELECTED_MODEL})..."):
                    # Generate topics only for selected lectures
                    generated_topic_lists = generate_topics(
                        chatbot, selected_lectures_list, st.session_state.min_topics, st.session_state.max_topics
                    )

                    # Format into dataframe
                    all_new_topics_list = []
                    for i, lecture in enumerate(selected_lectures_list):
                        for topic in generated_topic_lists[i]: # Access the corresponding list of topics
                            all_new_topics_list.append(
                                {
                                    "lecture_title": lecture["title"],
                                    "topic_title": topic["title"],
                                    "topic_description": topic["description"],
                                    "selected": True, # Default generated topics to selected
                                }
                            )

                    if all_new_topics_list:
                         new_topics_df = pd.DataFrame(all_new_topics_list)
                         # Append to existing topics_df or create if it doesn't exist/is empty
                         if "topics_df" not in st.session_state or st.session_state.topics_df.empty:
                             st.session_state.topics_df = new_topics_df
                         else:
                             # Avoid duplicates if regenerating for the same lectures? Maybe just replace?
                             # For simplicity, let's append for now. User can manage duplicates via editor.
                             # Or, more robustly: remove existing topics for the selected lectures, then append new ones.
                             existing_topics_df = st.session_state.topics_df
                             lectures_regenerated = selected_lectures_df['title'].tolist()
                             topics_to_keep = existing_topics_df[~existing_topics_df['lecture_title'].isin(lectures_regenerated)]
                             st.session_state.topics_df = pd.concat([topics_to_keep, new_topics_df], ignore_index=True)

                         st.success(f"Generated {len(all_new_topics_list)} topics for the selected lectures.")
                         st.rerun()
                    else:
                         st.warning("No topics were generated. This might be due to errors during generation for all selected lectures. Check logs above.")
            else:
                st.error(f"Failed to create chatbot for {CHATBOT_TYPE}. Check settings and API key.")

    # Display and manage topics
    if "topics_df" in st.session_state and not st.session_state.topics_df.empty:
        st.subheader("📋 Manage Topics")

        col1, col2, col3 = st.columns([1,1,5])
        with col1:
            if st.button("Select All", key="sel_all_top"):
                select_all_topics()
                st.rerun()
        with col2:
            if st.button("Select None", key="sel_none_top"):
                select_none_topics()
                st.rerun()

        # Filter topics based on selected lectures (optional, could be confusing)
        # lectures_filter = st.multiselect("Filter topics by lecture:", options=st.session_state.lecture_df['title'].unique())
        # display_topics_df = st.session_state.topics_df[st.session_state.topics_df['lecture_title'].isin(lectures_filter)] if lectures_filter else st.session_state.topics_df
        # Simpler: show all topics, user selects what they want regardless of lecture selection status

        st.session_state.topics_df = st.data_editor(
            st.session_state.topics_df,
            column_config={
                 "selected": st.column_config.CheckboxColumn("Select", default=False),
                 "lecture_title": st.column_config.TextColumn("Lecture", width="medium", disabled=True),
                 "topic_title": st.column_config.TextColumn("Topic Title", width="medium"),
                 "topic_description": st.column_config.TextColumn("Description", width="large"),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic", # Allow adding/deleting rows
            key="topic_editor"
        )

        # Add new topic functionality (manual add)
        st.subheader("➕ Add New Topic Manually")
        add_new_topic()

        # Download link for topics CSV
        st.markdown(
            get_table_download_link(
                st.session_state.topics_df, "topics.csv", "📥 Download Topics as CSV"
            ),
            unsafe_allow_html=True,
        )
    else:
        st.info("No topics loaded or generated yet. Generate topics for selected lectures or upload a CSV.")


with tab_notebooks:
    st.header("💻 Notebooks")

    # Check if topics exist and any are selected
    topics_available = "topics_df" in st.session_state and not st.session_state.topics_df.empty
    selected_topics_df = st.session_state.topics_df[st.session_state.topics_df["selected"]] if topics_available else pd.DataFrame()
    disable_gen_notebooks = selected_topics_df.empty or (SELECTED_MODEL is None)

    if st.button("💾 Create Selected Notebooks", key="gen_notebooks_btn", disabled=disable_gen_notebooks):
        if not SELECTED_MODEL:
             st.error("Please select a valid model in the Settings tab first.")
        elif not topics_available or selected_topics_df.empty:
            st.warning("Please select at least one topic in the 'Topics' tab before generating notebooks.")
        else:
            selected_topics_list = selected_topics_df.to_dict("records")
            # Prepare context: all topics grouped by lecture (could be large)
            # For simplicity, just pass all topics as JSON for now. Could optimize later.
            all_topics_context_df = st.session_state.get("topics_df", pd.DataFrame())
            topics_json_context = all_topics_context_df.to_json(orient="records", indent=2)

            chatbot = create_chatbot(CHATBOT_TYPE, SELECTED_MODEL, API_KEY_OVERRIDE, OLLAMA_ENDPOINT_OVERRIDE)
            if chatbot:
                os.makedirs(notebook_path, exist_ok=True)
                num_selected = len(selected_topics_list)
                st.info(f"Starting generation of {num_selected} notebook(s)...")
                progress_bar = st.progress(0)
                status_text = st.empty()
                success_count = 0
                fail_count = 0

                for i, topic in enumerate(selected_topics_list, 1):
                    # Sanitize title for filename
                    safe_title = re.sub(r'[^\w\-_\. ]', '_', topic['topic_title']) # Allow spaces, letters, numbers, underscore, hyphen, dot
                    safe_title = re.sub(r'\s+', '_', safe_title) # Replace spaces with underscores
                    filename = f"{i:02d}_{safe_title}.{st.session_state.extension}"
                    filepath = os.path.join(notebook_path, filename)

                    status_text.text(f"({i}/{num_selected}) Generating notebook: {filename}...")

                    # Skip if file already exists? Add a checkbox for overwrite? For now, let's overwrite.
                    # if os.path.exists(filepath):
                    #     st.write(f"Skipping existing file: {filename}")
                    #     progress_bar.progress(i / num_selected)
                    #     continue

                    nb_content = None
                    max_retries = 3
                    try_num = 1
                    while nb_content is None and try_num <= max_retries:
                         status_text.text(f"({i}/{num_selected}) Generating: {filename} (Attempt {try_num}/{max_retries})")
                         nb_content = create_notebook(
                              chatbot,
                              topic["lecture_title"],
                              topic["topic_title"],
                              topic["topic_description"],
                              topics_json_context, # Pass context
                              st.session_state.instructions, # Pass template from course tab
                              st.session_state.course_title,
                              st.session_state.examples_programming_language,
                              st.session_state.notebook_type,
                              st.session_state.libraries_used,
                         )
                         if nb_content is None:
                             st.warning(f"Attempt {try_num} failed for {filename}. Retrying...")
                             try_num += 1
                         else:
                             break # Success

                    if nb_content:
                        try:
                            with open(filepath, "w", encoding="utf-8") as f:
                                f.write(nb_content)
                            # st.success(f"({i}/{num_selected}) Created notebook: {filename}") # Make output less verbose
                            success_count += 1
                        except Exception as e:
                            st.error(f"Error writing notebook file {filename}: {str(e)}")
                            fail_count += 1
                    else:
                        st.error(
                            f"Failed to generate content for notebook: {filename} after {max_retries} attempts."
                        )
                        fail_count += 1

                    progress_bar.progress(i / num_selected)

                status_text.success(f"Notebook generation complete. Success: {success_count}, Failed: {fail_count}.")
                progress_bar.empty() # Remove progress bar


            else:
                st.error(f"Failed to create chatbot for {CHATBOT_TYPE}. Notebook generation cancelled.")

    # Display generated notebooks
    st.subheader("📂 Generated Notebooks")
    output_dir = notebook_path
    if os.path.exists(output_dir) and os.path.isdir(output_dir):
        try:
            # List notebooks matching the current extension setting
            notebooks = sorted([
                f for f in os.listdir(output_dir)
                if os.path.isfile(os.path.join(output_dir, f)) and f.endswith(f".{st.session_state.extension}")
            ])

            if notebooks:
                selected_notebook_file = st.selectbox(
                    f"Select a {st.session_state.extension} notebook to view/download:", notebooks
                )
                if selected_notebook_file:
                    selected_notebook_path = os.path.join(output_dir, selected_notebook_file)

                    # Display Content
                    st.write(f"**Preview of `{selected_notebook_file}`:**")
                    display_notebook(selected_notebook_path)

                    # Download link
                    try:
                        with open(selected_notebook_path, "rb") as fp:
                            st.download_button(
                                label=f"Download {selected_notebook_file}",
                                data=fp,
                                file_name=selected_notebook_file,
                                mime="application/octet-stream" # Generic type, browser might infer
                            )
                    except Exception as e:
                        st.error(f"Error preparing notebook for download: {e}")

            else:
                st.info(f"No notebooks with the extension '.{st.session_state.extension}' found in the output directory: '{output_dir}'. Generate some or check the path/extension setting.")
        except Exception as e:
             st.error(f"Error listing notebooks in '{output_dir}': {e}")
    else:
        st.info(f"Output directory '{output_dir}' not found. Generate notebooks first or check the path in Settings.")
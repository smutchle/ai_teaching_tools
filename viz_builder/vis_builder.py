import streamlit as st
import traceback
import sys
import io
import os
import json
import uuid
import time
from datetime import datetime
from contextlib import redirect_stdout, redirect_stderr
from dotenv import load_dotenv
from vt_ads_common.genai.GoogleChatBot import GoogleChatBot

load_dotenv()

def initialize_state():
    if "visualizations" not in st.session_state:
        if os.path.exists("visualizations.json"):
            with open("visualizations.json", "r") as f:
                st.session_state.visualizations = json.load(f)
        else:
            st.session_state.visualizations = {}
    
    if "current_viz_id" not in st.session_state:
        st.session_state.current_viz_id = None
    
    if "current_code" not in st.session_state:
        st.session_state.current_code = ""
    
    if "edit_history" not in st.session_state:
        st.session_state.edit_history = {}

def safe_exec_visualization(code):
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    local_vars = {}
    
    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            exec(code, {"st": st, "streamlit": st}, local_vars)
        
        return {
            "success": True,
            "stdout": stdout_buffer.getvalue(),
            "stderr": stderr_buffer.getvalue(),
            "locals": local_vars
        }
    except Exception as e:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        
        return {
            "success": False,
            "error_type": exc_type.__name__,
            "error_msg": str(e),
            "traceback": tb_str,
            "stdout": stdout_buffer.getvalue(),
            "stderr": stderr_buffer.getvalue()
        }

def repair_with_llm(code, error_info):
    chatbot = get_chatbot()
    
    prompt = f"""
    Fix this Streamlit visualization code that's throwing an error.
    
    ERROR TYPE: {error_info['error_type']}
    ERROR MESSAGE: {error_info['error_msg']}
    
    TRACEBACK:
    {error_info['traceback']}
    
    CURRENT CODE:
    ```python
    {code}
    ```
    
    Return only the complete fixed Python code with no explanations.
    """
    
    response = chatbot.complete(prompt)
    return extract_code_from_response(response)

def extract_code_from_response(response_text):
    if "```python" in response_text and "```" in response_text:
        start = response_text.find("```python") + 10
        end = response_text.find("```", start)
        return response_text[start:end].strip()
    elif "```" in response_text:
        start = response_text.find("```") + 3
        end = response_text.find("```", start)
        return response_text[start:end].strip()
    else:
        return response_text.strip()

def get_chatbot():
    api_key = os.getenv("GOOGLE_API_KEY")
    model_name = os.getenv("GOOGLE_MODEL").split(",")[0]
    return GoogleChatBot(api_key, model_name)

def save_visualization(viz_id=None, auto_save=False):
    if not viz_id:
        viz_id = str(uuid.uuid4())
    
    timestamp = datetime.now().isoformat()
    
    if not st.session_state.current_code:
        return viz_id
    
    if viz_id not in st.session_state.visualizations:
        st.session_state.visualizations[viz_id] = {
            "id": viz_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "code": st.session_state.current_code,
            "title": f"Visualization {len(st.session_state.visualizations) + 1}",
            "description": "",
            "tags": []
        }
    else:
        st.session_state.visualizations[viz_id]["updated_at"] = timestamp
        st.session_state.visualizations[viz_id]["code"] = st.session_state.current_code
    
    if viz_id not in st.session_state.edit_history:
        st.session_state.edit_history[viz_id] = []
    
    st.session_state.edit_history[viz_id].append({
        "timestamp": timestamp,
        "code": st.session_state.current_code,
        "auto_save": auto_save
    })
    
    with open("visualizations.json", "w") as f:
        json.dump(st.session_state.visualizations, f, indent=2)
    
    return viz_id

def create_new_visualization(prompt):
    chatbot = get_chatbot()
    
    generate_prompt = f"""
    Create a Streamlit visualization based on this description: "{prompt}"
    
    The code should:
    1. Be complete and runnable
    2. Include sample data if needed
    3. Be educational and interactive
    4. Use clear variable names
    5. Include UI elements for student interaction
    
    Return only the Python code, no explanations.
    """
    
    code = chatbot.complete(generate_prompt)
    code = extract_code_from_response(code)
    
    st.session_state.current_code = code
    viz_id = save_visualization()
    st.session_state.current_viz_id = viz_id
    
    return viz_id

def main():
    st.set_page_config(page_title="Educational Visualization Platform", layout="wide")
    initialize_state()
    
    st.title("Educational Visualization Platform")
    
    col1, col2 = st.columns([1, 3])
    
    with col1:
        st.subheader("Create Visualization")
        visualization_type = st.selectbox(
            "Type", 
            ["Data Visualization", "Simulation", "Interactive Exercise", "Concept Explorer"]
        )
        
        subject_area = st.selectbox(
            "Subject", 
            ["Math", "Science", "History", "Language Arts", "Computer Science", "Other"]
        )
        
        prompt = st.text_area("Describe the visualization you want:")
        
        if st.button("Generate"):
            with st.spinner("Generating visualization..."):
                create_new_visualization(f"{visualization_type} for {subject_area}: {prompt}")
        
        st.subheader("My Visualizations")
        for viz_id, viz in st.session_state.visualizations.items():
            if st.button(viz["title"], key=f"load_{viz_id}"):
                st.session_state.current_viz_id = viz_id
                st.session_state.current_code = viz["code"]
                st.experimental_rerun()
    
    with col2:
        if st.session_state.current_viz_id:
            viz = st.session_state.visualizations[st.session_state.current_viz_id]
            
            st.subheader(f"Editing: {viz['title']}")
            
            new_title = st.text_input("Title", viz["title"])
            if new_title != viz["title"]:
                viz["title"] = new_title
                save_visualization(st.session_state.current_viz_id, True)
            
            tabs = st.tabs(["Code", "Preview", "History"])
            
            with tabs[0]:
                new_code = st.text_area("Code Editor", st.session_state.current_code, height=400)
                if new_code != st.session_state.current_code:
                    st.session_state.current_code = new_code
                    save_visualization(st.session_state.current_viz_id, True)
            
            with tabs[1]:
                viz_container = st.container()
                with viz_container:
                    result = safe_exec_visualization(st.session_state.current_code)
                
                if not result["success"]:
                    st.error(f"Error: {result['error_msg']}")
                    with st.expander("Error Details"):
                        st.code(result["traceback"])
                    
                    if st.button("Auto-repair"):
                        with st.spinner("Repairing code..."):
                            fixed_code = repair_with_llm(st.session_state.current_code, result)
                            st.session_state.current_code = fixed_code
                            save_visualization(st.session_state.current_viz_id, True)
                            st.experimental_rerun()
            
            with tabs[2]:
                if st.session_state.current_viz_id in st.session_state.edit_history:
                    history = st.session_state.edit_history[st.session_state.current_viz_id]
                    for i, edit in enumerate(reversed(history)):
                        timestamp = datetime.fromisoformat(edit["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                        save_type = "Auto" if edit["auto_save"] else "Manual"
                        st.caption(f"{timestamp} ({save_type} save)")
                        if st.button(f"Restore version {len(history) - i}", key=f"restore_{i}"):
                            st.session_state.current_code = edit["code"]
                            save_visualization(st.session_state.current_viz_id)
                            st.experimental_rerun()
            
            refinement_prompt = st.text_area("Describe what to change:")
            if st.button("Refine with AI"):
                chatbot = get_chatbot()
                refine_prompt = f"""
                Refine this Streamlit visualization based on the following request: "{refinement_prompt}"
                
                Current Code:
                ```python
                {st.session_state.current_code}
                ```
                
                Return the complete updated code with all changes implemented. Return only Python code, no explanations.
                """
                
                with st.spinner("Refining visualization..."):
                    refined_code = chatbot.complete(refine_prompt)
                    refined_code = extract_code_from_response(refined_code)
                    st.session_state.current_code = refined_code
                    save_visualization(st.session_state.current_viz_id)
                    st.experimental_rerun()

if __name__ == "__main__":
    main()
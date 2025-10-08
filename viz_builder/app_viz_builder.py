import streamlit as st  # 🎈
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
from AnthropicChatBot import AnthropicChatBot
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns
import plotly
import plotly.express as px
import plotly.graph_objects as go
import plotly.figure_factory as ff
import scipy
import math
import random
import datetime
import time
import json
import re
import os
import sys
import io
import collections
import itertools
import sklearn
import statsmodels
import statsmodels.api as sm
import altair as alt
import pygame
import shutil

# Add any other common visualization libraries you might need
# import altair as alt
# import bokeh
# import folium

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
        
    if "uploaded_csv" not in st.session_state:
        st.session_state.uploaded_csv = None
    
    if "uploaded_image" not in st.session_state:
        st.session_state.uploaded_image = None

    # Create upload directory if it doesn't exist
    os.makedirs("./upload", exist_ok=True)

def safe_exec_visualization(code):
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    local_vars = {}
    
    # Dictionary of top-level modules to make available
    exec_globals = {
        "__builtins__": __builtins__,
        "st": st,
        "streamlit": st,
        "np": np,
        "pd": pd,
        "plt": plt,
        "mpl": mpl,
        "matplotlib": mpl,
        "sns": sns,
        "seaborn": sns,
        "plotly": plotly,
        "px": px,
        "go": go,
        "ff": ff,
        "scipy": scipy,
        "math": math,
        "random": random,
        "datetime": datetime,
        "time": time,
        "json": json,
        "re": re,
        "os": os,
        "sys": sys,
        "io": io,
        "collections": collections,
        "itertools": itertools,
        "sklearn": sklearn,
        "statsmodels": statsmodels,
        "sm": sm,
        "alt": alt,
        "norm": scipy.stats.norm,
        "pygame": pygame,
    }
    
    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            placeholder = st.empty()
            with placeholder.container():
                exec(code, exec_globals, local_vars)
        
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

def repair_with_llm(code, error_info, image_filename=None):
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

    These imports are the only allowed imports.  You must work within these import libraries.
    ```
        import streamlit as st
        import numpy as np
        import pandas as pd
        import matplotlib as mpl
        import matplotlib.pyplot as plt
        import seaborn as sns
        import plotly
        import plotly.express as px
        import plotly.graph_objects as go
        import plotly.figure_factory as ff
        import scipy
        import math
        import random
        import datetime
        import time
        import json
        import re
        import os
        import sys
        import io
        import collections
        import itertools
        import sklearn
        import statsmodels
        import statsmodels.api as sm
        import altair as alt
        import pygame
    ```

    Return only the complete fixed Python code with no explanations. Make sure every module used is imported within the code itself.

    CRITICAL: DO NOT include any st.set_option() calls in your code.
    DO NOT use st.set_option('deprecation.showPyplotGlobalUse', False) - this option no longer exists in Streamlit and will cause an error.
    DO NOT use st.set_page_config() as it's already set by the parent app.  
    """

    # Use image if available for repair
    if image_filename:
        image_path = os.path.join("./upload", image_filename)
        if os.path.exists(image_path):
            response = chatbot.complete_with_image(prompt, image_path)
        else:
            response = chatbot.complete(prompt)
    else:
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
    api_key = os.getenv("CLAUDE_API_KEY")
    model_name = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
    return AnthropicChatBot(api_key, model=model_name)

def save_visualization(viz_id=None, auto_save=False, title=None):
    if not viz_id:
        viz_id = str(uuid.uuid4())

    timestamp = datetime.datetime.now().isoformat()

    if not st.session_state.current_code:
        return viz_id

    if not title:
        title = f"Visualization {len(st.session_state.visualizations) + 1}"

    if viz_id not in st.session_state.visualizations:
        st.session_state.visualizations[viz_id] = {
            "id": viz_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "code": st.session_state.current_code,
            "title": title,
            "description": "",
            "tags": [],
            "used_csv": st.session_state.uploaded_csv,
            "used_image": st.session_state.uploaded_image
        }
    else:
        st.session_state.visualizations[viz_id]["updated_at"] = timestamp
        st.session_state.visualizations[viz_id]["code"] = st.session_state.current_code
        st.session_state.visualizations[viz_id]["title"] = title
        st.session_state.visualizations[viz_id]["used_csv"] = st.session_state.uploaded_csv
        st.session_state.visualizations[viz_id]["used_image"] = st.session_state.uploaded_image

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

def generate_title_from_code(code, image_filename=None):
    chatbot = get_chatbot()
    prompt = f"""
    Suggest a concise and descriptive title for a Streamlit visualization based on the following code. The title should be under 10 words.
    
    CODE:
    ```python
    {code}
    ```

    Return only the title.
    """

    # Use image if available for title generation
    if image_filename:
        image_path = os.path.join("./upload", image_filename)
        if os.path.exists(image_path):
            title = chatbot.complete_with_image(prompt, image_path)
        else:
            title = chatbot.complete(prompt)
    else:
        title = chatbot.complete(prompt)
    
    return title.strip().replace('"', '')

def generate_quarto_title(code, image_filename=None):
    chatbot = get_chatbot()
    prompt = f"""
    Suggest an academic, descriptive title for a Quarto document containing this Streamlit visualization.
    The title should be formal, clear, and suitable for educational or research contexts.
    
    CODE:
    ```python
    {code}
    ```

    Return only the title with no quotes or additional text.
    """

    # Use image if available for title generation
    if image_filename:
        image_path = os.path.join("./upload", image_filename)
        if os.path.exists(image_path):
            title = chatbot.complete_with_image(prompt, image_path)
        else:
            title = chatbot.complete(prompt)
    else:
        title = chatbot.complete(prompt)
    
    return title.strip().replace('"', '')

def export_to_quarto(viz_id):
    viz = st.session_state.visualizations[viz_id]
    code = viz['code']
    image_filename = viz.get('used_image')
    
    # Generate an academic title for the Quarto document
    title = generate_quarto_title(code, image_filename)
    
    qmd_content = f"""---
title: "{title}"
format: html
---

```{{python}}
{code}
```
"""
    return qmd_content, title

def create_new_visualization(prompt, csv_filename=None, image_filename=None):
    chatbot = get_chatbot()
    
    # Include CSV data information in the prompt if a file was uploaded
    csv_data_section = ""
    if csv_filename:
        try:
            # Read the CSV file to include sample data in the prompt
            csv_path = os.path.join("./upload", csv_filename)
            df = pd.read_csv(csv_path)
            sample_data = df.head(50).to_csv(index=False)
            
            csv_data_section = f"""
            A CSV file has been uploaded and saved at './upload/{csv_filename}'.
            
            Here's a sample of the first 50 rows:
            ```
            {sample_data}
            ```
            
            Please incorporate this data in your visualization. Your code should load the data from the file path './upload/{csv_filename}'.
            """
        except Exception as e:
            csv_data_section = f"Note: There was an error reading the CSV file: {str(e)}"
    
    # Include image reference in prompt
    image_section = ""
    if image_filename:
        image_section = """
        
        An example visualization image has been uploaded. Please create a visualization that follows the style, layout, and visual approach shown in the reference image while incorporating the specific requirements from the text prompt.
        """
    
    generate_prompt = f"""
    Create a Streamlit visualization based on this description: "{prompt}"
    {image_section}
    {csv_data_section}

    IMPORTANT: The code MUST include ALL imports it needs to run. DO NOT rely on any pre-existing imports. Do not write any functions. All codes must be inline.
    
    The code should:
    1. Begin with ALL required imports at the top (mandatory)
    2. Be complete and self-contained - do not rely on any external variables or imports
    3. {"Load data from './upload/" + csv_filename + "'" if csv_filename else "Include sample data if needed (generated or loaded within the code)"}
    4. Be educational and interactive
    5. Use clear variable names
    6. Include UI elements for student interaction
    7. Use streamlit's rerun() instead of experimental_rerun()
    8. Streamlit image use_column_width=True has been replaced with use_container_width=True. Do not use use_column_width.
    9. Don't write any functions, all code must be in-line (it will be run in an eval() call)
    10. Suppress any warnings.

   These imports are the only allowed imports. You must work within these import libraries.
    ```
        import streamlit as st
        import numpy as np
        import pandas as pd
        import matplotlib as mpl
        import matplotlib.pyplot as plt
        import seaborn as sns
        import plotly
        import plotly.express as px
        import plotly.graph_objects as go
        import plotly.figure_factory as ff
        import scipy
        import math
        import random
        import datetime
        import time
        import json
        import re
        import os
        import sys
        import io
        import collections
        import itertools
        import sklearn
        import statsmodels
        import statsmodels.api as sm
        import altair as alt
        import pygame
    ```
    Do not write any functions. All codes must be inline.

    CRITICAL: DO NOT include any st.set_option() calls in your code.
    DO NOT use st.set_option('deprecation.showPyplotGlobalUse', False) - this option no longer exists in Streamlit and will cause an error.
    DO NOT use st.set_page_config() as it's already set by the parent app.

    Return only the complete Python code with no explanations.
    """

    # Use appropriate method based on whether image is available
    if image_filename:
        image_path = os.path.join("./upload", image_filename)
        if os.path.exists(image_path):
            code = chatbot.complete_with_image(generate_prompt, image_path)
        else:
            code = chatbot.complete(generate_prompt)
    else:
        code = chatbot.complete(generate_prompt)
    
    code = extract_code_from_response(code)

    st.session_state.current_code = code
    title = generate_title_from_code(code, image_filename)
    viz_id = save_visualization(title=title)
    st.session_state.current_viz_id = viz_id

    return viz_id

def delete_visualization(viz_id):
    if viz_id in st.session_state.visualizations:
        del st.session_state.visualizations[viz_id]
        with open("visualizations.json", "w") as f:
            json.dump(st.session_state.visualizations, f, indent=2)
        st.session_state.current_viz_id = None
        st.session_state.current_code = ""
        st.rerun()

def handle_csv_upload(uploaded_file):
    if uploaded_file is not None:
        # Create upload directory if it doesn't exist
        os.makedirs("./upload", exist_ok=True)
        
        # Save the file to the upload directory
        file_path = os.path.join("./upload", uploaded_file.name)
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
            
        st.session_state.uploaded_csv = uploaded_file.name
        return True, f"File '{uploaded_file.name}' uploaded successfully and saved to {file_path}"
    
    return False, "No file uploaded"

def handle_image_upload(uploaded_file):
    if uploaded_file is not None:
        # Create upload directory if it doesn't exist
        os.makedirs("./upload", exist_ok=True)
        
        # Save the file to the upload directory
        file_path = os.path.join("./upload", uploaded_file.name)
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
            
        st.session_state.uploaded_image = uploaded_file.name
        return True, f"Image '{uploaded_file.name}' uploaded successfully and saved to {file_path}"
    
    return False, "No image uploaded"

def main():
    st.set_page_config(page_title="Educational Visualization Platform", layout="wide") # 🖥️
    initialize_state()

    st.title("🎓 Educational Visualization Platform")

    st.warning("⚠️ Do not upload personal information or trade secrets.")

    sidebar, main_area = st.columns([1, 3])

    with sidebar:
        st.subheader("✨ Create Visualization")

        # File uploader for CSV
        uploaded_csv = st.file_uploader("📊 Upload CSV Dataset (optional)", type=["csv"])
        
        # Handle CSV file upload
        if uploaded_csv is not None:
            success, message = handle_csv_upload(uploaded_csv)
            if success:
                st.success(message)
            else:
                st.error(message)

        # File uploader for example visualization image
        uploaded_image = st.file_uploader("🖼️ Upload Example Visualization (optional)", type=["png", "jpg", "jpeg", "gif", "bmp"])
        
        # Handle image file upload
        if uploaded_image is not None:
            success, message = handle_image_upload(uploaded_image)
            if success:
                st.success(message)
                # Show a preview of the uploaded image
                with st.expander("Preview Example Image", expanded=True):
                    st.image(uploaded_image, caption="Example Visualization", use_container_width=True)
            else:
                st.error(message)

        prompt = st.text_area("📝 Describe the visualization you want:")

        if st.button("✨ Generate"):
            with st.spinner("Generating visualization..."):  # ⏳
                # Pass both CSV and image filenames to the create_new_visualization function
                create_new_visualization(prompt, st.session_state.uploaded_csv, st.session_state.uploaded_image)

        st.subheader("📚 My Visualizations")

        # Create a list of visualization titles for the selectbox
        visualization_titles = ["Select a Visualization"] + [viz["title"] for viz in st.session_state.visualizations.values()]

        # Use a selectbox to choose the visualization
        selected_title = st.selectbox("🗂️ Choose a Visualization", visualization_titles)

        # Find the visualization ID based on the selected title
        selected_viz_id = None
        for viz_id, viz in st.session_state.visualizations.items():
            if viz["title"] == selected_title:
                selected_viz_id = viz_id
                break

        # Load and Delete Buttons
        if selected_viz_id:
            col1, col2 = st.columns(2)  # Create two columns for the buttons
            with col1:
                if st.button("📂 Load"):
                    st.session_state.current_viz_id = selected_viz_id
                    st.session_state.current_code = st.session_state.visualizations[selected_viz_id]["code"]
                    st.session_state.uploaded_csv = st.session_state.visualizations[selected_viz_id].get("used_csv")
                    st.session_state.uploaded_image = st.session_state.visualizations[selected_viz_id].get("used_image")
                    st.rerun()
            with col2:
                if st.button("🗑️ Delete"):
                    delete_visualization(selected_viz_id)

    with main_area:
        if st.session_state.current_viz_id:
            viz = st.session_state.visualizations[st.session_state.current_viz_id]

            # Display file information if this visualization uses files
            file_info_items = []
            if "used_csv" in viz and viz["used_csv"]:
                file_info_items.append(f"CSV: {viz['used_csv']}")
            if "used_image" in viz and viz["used_image"]:
                file_info_items.append(f"Example Image: {viz['used_image']}")
            
            if file_info_items:
                st.info(f"ℹ️ This visualization uses: {', '.join(file_info_items)}")

            # Parameters Section (Collapsible)
            with st.expander(f"⚙️ Parameters for: {viz['title']}", expanded=True):
                new_title = st.text_input("Title", viz["title"])
                if new_title != viz["title"]:
                    viz["title"] = new_title
                    save_visualization(st.session_state.current_viz_id, True, title=new_title)

            tabs = st.tabs(["💻 Code", "👁️ Preview", "📜 History", "📤 Export"])

            with tabs[0]:
                new_code = st.text_area("Code Editor", st.session_state.current_code, height=400)
                if new_code != st.session_state.current_code:
                    st.session_state.current_code = new_code
                    save_visualization(st.session_state.current_viz_id, True, title=viz['title'])

            with tabs[1]:
                # Use st.empty() to create a placeholder for the visualization.
                # This allows the preview to be cleared and updated on each run.
                viz_placeholder = st.empty()
                with viz_placeholder.container(): # execute in a container
                    result = safe_exec_visualization(st.session_state.current_code)

                if not result["success"]:
                    st.error(f"❌ Error: {result['error_msg']}")
                    with st.expander("ℹ️ Error Details"):
                        st.code(result["traceback"])

                    if st.button("🛠️ Auto-repair"):
                        with st.spinner("⏳ Repairing code..."):
                            fixed_code = repair_with_llm(st.session_state.current_code, result, viz.get('used_image'))
                            st.session_state.current_code = fixed_code
                            save_visualization(st.session_state.current_viz_id, True, title=viz['title'])
                            st.rerun()

            with tabs[2]:
                if st.session_state.current_viz_id in st.session_state.edit_history:
                    history = st.session_state.edit_history[st.session_state.current_viz_id]
                    for i, edit in enumerate(reversed(history)):
                        timestamp = datetime.datetime.fromisoformat(edit["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                        save_type = "Auto" if edit["auto_save"] else "Manual"
                        st.caption(f"⏱️ {timestamp} ({save_type} save)")
                        if st.button(f"🔄 Restore version {len(history) - i}", key=f"restore_{i}"):
                            st.session_state.current_code = edit["code"]
                            save_visualization(st.session_state.current_viz_id, title=viz['title'])
                            st.rerun()
            
            with tabs[3]:  # Export tab
                if st.button("📥 Export as Quarto (.qmd)"):
                    with st.spinner("🔄 Generating academic title..."):
                        qmd_content, quarto_title = export_to_quarto(st.session_state.current_viz_id)
                        st.success(f"✅ Generated title: {quarto_title}")
                        st.download_button(
                            label="⬇️ Download Quarto File",
                            data=qmd_content,
                            file_name=f"{quarto_title.replace(' ', '_')}.qmd",
                            mime="text/markdown",
                        )

            refinement_prompt = st.text_area("✍️ Describe what to change:")
            if st.button("🤖 Refine with AI"):
                chatbot = get_chatbot()
                
                # Include information about the files if used
                file_info = ""
                if "used_csv" in viz and viz["used_csv"]:
                    file_info += f"\nThis visualization uses CSV data from './upload/{viz['used_csv']}'. Make sure to maintain this data source in your refinements."
                if "used_image" in viz and viz["used_image"]:
                    file_info += f"\nThis visualization was created using an example image reference. Consider the visual style and layout from the reference image."
                
                refine_prompt = f"""
                Refine this Streamlit visualization based on the following request: "{refinement_prompt}"
                {file_info}

                Current Code:
                ```python
                {st.session_state.current_code}
                ```

               These imports are the only allowed imports.  You must work within these import libraries.
                ```
                    import streamlit as st
                    import numpy as np
                    import pandas as pd
                    import matplotlib as mpl
                    import matplotlib.pyplot as plt
                    import seaborn as sns
                    import plotly
                    import plotly.express as px
                    import plotly.graph_objects as go
                    import plotly.figure_factory as ff
                    import scipy
                    import math
                    import random
                    import datetime
                    import time
                    import json
                    import re
                    import os
                    import sys
                    import io
                    import collections
                    import itertools
                    import sklearn
                    import statsmodels
                    import statsmodels.api as sm
                    import altair as alt
                    import pygame
                ```

                Return the complete updated code with all changes implemented and all required imports.
                Return only Python code, no explanations or comments about the changes.
                Do not write any functions. All codes must be inline.

                CRITICAL: DO NOT include any st.set_option() calls in your code.
                DO NOT use st.set_option('deprecation.showPyplotGlobalUse', False) - this option no longer exists in Streamlit and will cause an error.
                DO NOT use st.set_page_config() as it's already set by the parent app.
                """

                with st.spinner("⏳ Refining visualization..."):
                    # Use image if available for refinement
                    image_filename = viz.get('used_image')
                    if image_filename:
                        image_path = os.path.join("./upload", image_filename)
                        if os.path.exists(image_path):
                            refined_code = chatbot.complete_with_image(refine_prompt, image_path)
                        else:
                            refined_code = chatbot.complete(refine_prompt)
                    else:
                        refined_code = chatbot.complete(refine_prompt)
                    
                    refined_code = extract_code_from_response(refined_code)
                    st.session_state.current_code = refined_code
                    title = generate_title_from_code(refined_code, image_filename)  # Generate a new title based on refined code
                    save_visualization(st.session_state.current_viz_id, title=title)
                    st.rerun()

if __name__ == "__main__":
    main()
    
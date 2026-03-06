import io
import os
import re
import subprocess
import zipfile

import pandas as pd
import streamlit as st

from chatbot_factory import create_chatbot
from generators import create_notebook


def render():
    st.header("Outputs")

    topics_available = "topics_df" in st.session_state and not st.session_state.topics_df.empty
    selected_topics_df = (
        st.session_state.topics_df[st.session_state.topics_df["selected"]]
        if topics_available
        else pd.DataFrame()
    )
    selected_model = st.session_state.selected_model
    disable_gen_notebooks = selected_topics_df.empty or (selected_model is None)

    if st.button("Create Selected Outputs", key="gen_notebooks_btn", disabled=disable_gen_notebooks):
        if not selected_model:
            st.error("Please select a valid model in the Settings tab first.")
        elif not topics_available or selected_topics_df.empty:
            st.warning("Please select at least one topic in the 'Topics' tab before generating outputs.")
        else:
            selected_topics_list = selected_topics_df.to_dict("records")
            all_topics_context_df = st.session_state.get("topics_df", pd.DataFrame())
            topics_json_context = all_topics_context_df.to_json(orient="records", indent=2)
            output_path = st.session_state.output_path

            chatbot = create_chatbot(
                st.session_state.selected_provider,
                selected_model,
                st.session_state.api_key_input,
                st.session_state.ollama_endpoint_input,
            )
            if chatbot:
                os.makedirs(output_path, exist_ok=True)
                num_selected = len(selected_topics_list)
                st.info(f"Starting generation of {num_selected} output(s)...")
                progress_bar = st.progress(0)
                status_text = st.empty()
                success_count = 0
                fail_count = 0

                for i, topic in enumerate(selected_topics_list, 1):
                    safe_title = re.sub(r'[^\w\-_\. ]', '_', topic['topic_title'])
                    safe_title = re.sub(r'\s+', '_', safe_title)
                    filename = f"{i:02d}_{safe_title}.{st.session_state.extension}"
                    filepath = os.path.join(output_path, filename)

                    status_text.text(f"({i}/{num_selected}) Generating output: {filename}...")

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
                            topics_json_context,
                            st.session_state.instructions,
                            st.session_state.course_title,
                            st.session_state.examples_programming_language,
                            st.session_state.notebook_type,
                            st.session_state.libraries_used,
                        )
                        if nb_content is None:
                            st.warning(f"Attempt {try_num} failed for {filename}. Retrying...")
                            try_num += 1
                        else:
                            break

                    if nb_content:
                        try:
                            if st.session_state.notebook_type == "PowerPoint (pptx)":
                                qmd_filename = f"{i:02d}_{safe_title}.qmd"
                                qmd_filepath = os.path.join(output_path, qmd_filename)
                                with open(qmd_filepath, "w", encoding="utf-8") as f:
                                    f.write(nb_content)
                                status_text.text(f"({i}/{num_selected}) Rendering {qmd_filename} to PowerPoint...")
                                render_result = subprocess.run(
                                    ["quarto", "render", qmd_filepath, "--to", "pptx"],
                                    capture_output=True,
                                    text=True,
                                )
                                if render_result.returncode != 0:
                                    st.error(f"Quarto render failed for {qmd_filename}:\n{render_result.stderr}")
                                    fail_count += 1
                                else:
                                    success_count += 1
                            else:
                                with open(filepath, "w", encoding="utf-8") as f:
                                    f.write(nb_content)
                                success_count += 1
                        except Exception as e:
                            st.error(f"Error writing output file {filename}: {str(e)}")
                            fail_count += 1
                    else:
                        st.error(f"Failed to generate content for output: {filename} after {max_retries} attempts.")
                        fail_count += 1

                    progress_bar.progress(i / num_selected)

                status_text.success(f"Output generation complete. Success: {success_count}, Failed: {fail_count}.")
                progress_bar.empty()
            else:
                st.error(f"Failed to create chatbot for {st.session_state.selected_provider}. Output generation cancelled.")

    st.subheader("Generated Outputs")
    output_dir = st.session_state.output_path
    if os.path.exists(output_dir) and os.path.isdir(output_dir):
        try:
            notebooks = sorted([
                f for f in os.listdir(output_dir)
                if os.path.isfile(os.path.join(output_dir, f)) and f.endswith(f".{st.session_state.extension}")
            ])

            if notebooks:
                try:
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fname in os.listdir(output_dir):
                            fpath = os.path.join(output_dir, fname)
                            if os.path.isfile(fpath):
                                zf.write(fpath, arcname=fname)
                    zip_buffer.seek(0)
                    st.download_button(
                        label="Download All as ZIP",
                        data=zip_buffer,
                        file_name="notebooks.zip",
                        mime="application/zip",
                    )
                except Exception as e:
                    st.error(f"Error creating ZIP: {e}")

                selected_notebook_file = st.selectbox(
                    f"Select a {st.session_state.extension} file to download:", notebooks
                )
                if selected_notebook_file:
                    selected_notebook_path = os.path.join(output_dir, selected_notebook_file)
                    try:
                        with open(selected_notebook_path, "rb") as fp:
                            if selected_notebook_file.endswith(".pptx"):
                                mime_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                            else:
                                mime_type = "application/octet-stream"
                            st.download_button(
                                label=f"Download {selected_notebook_file}",
                                data=fp,
                                file_name=selected_notebook_file,
                                mime=mime_type,
                            )
                    except Exception as e:
                        st.error(f"Error preparing file for download: {e}")
            else:
                st.info(
                    f"No output files with extension '.{st.session_state.extension}' found in "
                    f"the output directory: '{output_dir}'. Generate some or check the path/extension setting."
                )
        except Exception as e:
            st.error(f"Error listing outputs in '{output_dir}': {e}")
    else:
        st.info(f"Output directory '{output_dir}' not found. Generate outputs first or check the path in Settings.")

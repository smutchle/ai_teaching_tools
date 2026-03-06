import base64

import pandas as pd
import streamlit as st


def upload_lectures():
    uploaded_file = st.file_uploader("Upload Modules CSV", type="csv")
    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
            required_columns = {"title", "description"}
            if not all(col in df.columns for col in required_columns):
                st.error("CSV must contain 'title' and 'description' columns")
                return

            df["selected"] = False
            st.session_state.lecture_df = df
            st.success("Modules uploaded successfully!")
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
        if notebook_path.endswith(".ipynb"):
            st.code(content, language="json")
        elif notebook_path.endswith(".qmd"):
            st.markdown(f"```markdown\n{content}\n```")
        elif notebook_path.endswith(".pptx"):
            st.info("Preview is not available for PowerPoint files. Use the download button below to open in PowerPoint.")
        else:
            st.text(content)
    except FileNotFoundError:
        st.error(f"Error: File not found at {notebook_path}")
    except Exception as e:
        st.error(f"Error reading notebook: {e}")


def add_new_lecture():
    with st.form(key="add_lecture_form"):
        new_title = st.text_input("New Module Title")
        new_description = st.text_area("New Module Description")
        submit_button = st.form_submit_button(label="Add New Module")

    if submit_button:
        if not new_title or not new_description:
            st.warning("Please provide both a title and description for the new module.")
            return
        new_lecture = {
            "title": new_title,
            "description": new_description,
            "selected": True,
        }
        if "lecture_df" not in st.session_state or st.session_state.lecture_df is None:
            st.session_state.lecture_df = pd.DataFrame([new_lecture])
        else:
            st.session_state.lecture_df = pd.concat(
                [st.session_state.lecture_df, pd.DataFrame([new_lecture])],
                ignore_index=True,
            )
        st.success(f"Module '{new_title}' added.")
        st.rerun()


def add_new_topic():
    with st.form(key="add_topic_form"):
        if "lecture_df" in st.session_state and not st.session_state.lecture_df.empty:
            lectures = st.session_state.lecture_df["title"].unique()
            if len(lectures) > 0:
                new_lecture = st.selectbox("Select Module", lectures)
            else:
                st.warning("No modules available to add topics to. Please add or generate modules first.")
                new_lecture = None
        else:
            st.warning("No modules available. Please add or generate modules first.")
            new_lecture = None

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
            "selected": True,
        }
        if "topics_df" not in st.session_state or st.session_state.topics_df is None:
            st.session_state.topics_df = pd.DataFrame([new_topic])
        else:
            st.session_state.topics_df = pd.concat(
                [st.session_state.topics_df, pd.DataFrame([new_topic])], ignore_index=True
            )
        st.success(f"Topic '{new_title}' added to module '{new_lecture}'.")
        st.rerun()

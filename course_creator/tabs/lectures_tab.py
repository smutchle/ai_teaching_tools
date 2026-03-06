import pandas as pd
import streamlit as st

from chatbot_factory import create_chatbot
from generators import generate_lectures
from rag.session_rag import get_session_rag_context
from ui_components import (
    add_new_lecture,
    get_table_download_link,
    select_all_lectures,
    select_none_lectures,
    upload_lectures,
)


def render():
    st.header("Modules")
    st.subheader("Upload Modules (Optional)")
    upload_lectures()

    selected_model = st.session_state.selected_model

    st.subheader("Generate Modules")
    if st.button("Generate Modules", key="gen_lectures_btn", disabled=(selected_model is None)):
        if not selected_model:
            st.error("Please select a valid model in the Settings tab first.")
        else:
            chatbot = create_chatbot(
                st.session_state.selected_provider,
                selected_model,
                st.session_state.api_key_input,
                st.session_state.ollama_endpoint_input,
            )
            if chatbot:
                with st.spinner(
                    f"Generating {st.session_state.num_lectures} modules using "
                    f"{st.session_state.selected_provider} ({selected_model})..."
                ):
                    rag_query = f"Course: {st.session_state.course_title}. {st.session_state.course_description}"
                    rag_context = get_session_rag_context(rag_query)
                    lectures = generate_lectures(
                        chatbot,
                        st.session_state.course_title,
                        st.session_state.course_description,
                        st.session_state.num_lectures,
                        st.session_state.lecture_length,
                        st.session_state.get('lecture_level', 'graduate'),
                        rag_context=rag_context,
                    )
                    if lectures:
                        df = pd.DataFrame(lectures)
                        df["selected"] = True
                        st.session_state.lecture_df = df
                        st.success(f"{len(lectures)} Modules generated successfully!")
                        if 'topics_df' in st.session_state:
                            st.session_state.topics_df = pd.DataFrame(columns=st.session_state.topics_df.columns)
                            st.info("Existing topics cleared as modules were regenerated.")
                        st.rerun()
                    else:
                        st.warning("Module generation failed. Please check the error messages above and your settings/API key.")
            else:
                st.error(f"Failed to create chatbot for {st.session_state.selected_provider}. Check settings and API key.")

    if "lecture_df" in st.session_state and not st.session_state.lecture_df.empty:
        st.subheader("Manage Modules")

        col1, col2, col3 = st.columns([1, 1, 5])
        with col1:
            if st.button("Select All", key="sel_all_lec"):
                select_all_lectures()
                st.rerun()
        with col2:
            if st.button("Select None", key="sel_none_lec"):
                select_none_lectures()
                st.rerun()

        st.session_state.lecture_df = st.data_editor(
            st.session_state.lecture_df,
            column_config={
                "selected": st.column_config.CheckboxColumn("Select", default=False),
                "title": st.column_config.TextColumn("Module Title", width="medium"),
                "description": st.column_config.TextColumn("Description", width="large"),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="lecture_editor",
        )

        st.subheader("Add New Module Manually")
        add_new_lecture()

        st.markdown(
            get_table_download_link(st.session_state.lecture_df, "modules.csv", "Download Modules as CSV"),
            unsafe_allow_html=True,
        )
    else:
        st.info("No modules loaded or generated yet. Use the options above.")

import pandas as pd
import streamlit as st

from chatbot_factory import create_chatbot
from generators import generate_topics
from ui_components import (
    add_new_topic,
    get_table_download_link,
    select_all_topics,
    select_none_topics,
    upload_topics,
)


def render():
    st.header("Topics")
    st.subheader("Upload Topics (Optional)")
    upload_topics()

    st.subheader("Topic Generation Settings")
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.min_topics = st.number_input(
            "Min topics per module:", min_value=1, max_value=10,
            value=st.session_state.get('min_topics', 1), step=1,
        )
    with col2:
        st.session_state.max_topics = st.number_input(
            "Max topics per module:", min_value=st.session_state.get('min_topics', 1), max_value=30,
            value=st.session_state.get('max_topics', 8), step=1,
        )

    selected_model = st.session_state.selected_model
    lectures_available = "lecture_df" in st.session_state and not st.session_state.lecture_df.empty
    selected_lectures_df = (
        st.session_state.lecture_df[st.session_state.lecture_df["selected"]]
        if lectures_available
        else pd.DataFrame()
    )
    disable_gen_topics = selected_lectures_df.empty or (selected_model is None)

    st.subheader("Generate Topics for Selected Modules")
    if st.button("Generate Topics", key="gen_topics_btn", disabled=disable_gen_topics):
        if not selected_model:
            st.error("Please select a valid model in the Settings tab first.")
        elif not lectures_available or selected_lectures_df.empty:
            st.warning("Please select at least one module in the 'Modules' tab before generating topics.")
        else:
            selected_lectures_list = selected_lectures_df.to_dict("records")
            chatbot = create_chatbot(
                st.session_state.selected_provider,
                selected_model,
                st.session_state.api_key_input,
                st.session_state.ollama_endpoint_input,
            )
            if chatbot:
                with st.spinner(
                    f"Generating topics for {len(selected_lectures_list)} selected module(s) using "
                    f"{st.session_state.selected_provider} ({selected_model})..."
                ):
                    generated_topic_lists = generate_topics(
                        chatbot, selected_lectures_list,
                        st.session_state.min_topics, st.session_state.max_topics,
                    )

                    all_new_topics_list = []
                    for i, lecture in enumerate(selected_lectures_list):
                        for topic in generated_topic_lists[i]:
                            all_new_topics_list.append({
                                "lecture_title": lecture["title"],
                                "topic_title": topic["title"],
                                "topic_description": topic["description"],
                                "selected": True,
                            })

                    if all_new_topics_list:
                        new_topics_df = pd.DataFrame(all_new_topics_list)
                        if "topics_df" not in st.session_state or st.session_state.topics_df.empty:
                            st.session_state.topics_df = new_topics_df
                        else:
                            existing_topics_df = st.session_state.topics_df
                            lectures_regenerated = selected_lectures_df['title'].tolist()
                            topics_to_keep = existing_topics_df[
                                ~existing_topics_df['lecture_title'].isin(lectures_regenerated)
                            ]
                            st.session_state.topics_df = pd.concat(
                                [topics_to_keep, new_topics_df], ignore_index=True
                            )
                        st.success(f"Generated {len(all_new_topics_list)} topics for the selected modules.")
                        st.rerun()
                    else:
                        st.warning("No topics were generated. This might be due to errors during generation for all selected modules. Check logs above.")
            else:
                st.error(f"Failed to create chatbot for {st.session_state.selected_provider}. Check settings and API key.")

    if "topics_df" in st.session_state and not st.session_state.topics_df.empty:
        st.subheader("Manage Topics")

        col1, col2, col3 = st.columns([1, 1, 5])
        with col1:
            if st.button("Select All", key="sel_all_top"):
                select_all_topics()
                st.rerun()
        with col2:
            if st.button("Select None", key="sel_none_top"):
                select_none_topics()
                st.rerun()

        st.session_state.topics_df = st.data_editor(
            st.session_state.topics_df,
            column_config={
                "selected": st.column_config.CheckboxColumn("Select", default=False),
                "lecture_title": st.column_config.TextColumn("Module", width="medium", disabled=True),
                "topic_title": st.column_config.TextColumn("Topic Title", width="medium"),
                "topic_description": st.column_config.TextColumn("Description", width="large"),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="topic_editor",
        )

        st.subheader("Add New Topic Manually")
        add_new_topic()

        st.markdown(
            get_table_download_link(st.session_state.topics_df, "topics.csv", "Download Topics as CSV"),
            unsafe_allow_html=True,
        )
    else:
        st.info("No topics loaded or generated yet. Generate topics for selected modules or upload a CSV.")

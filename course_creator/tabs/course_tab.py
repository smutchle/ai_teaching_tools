import streamlit as st

DEFAULT_INSTRUCTIONS = """
Include the following sections:

1.  **Topic Overview**:
    *   Provide a detailed overview, focusing on building intuition.
    *   Conclude with the importance and relevance of this topic within the broader lecture/course ({course_title}).
2.  **Background & Theory**:
    *   This section must be the bulk of the document. It should be comprehensive, detailed and packed with information.  It should take 30-60 minutes to cover this Background & Theory section.
    *   Cover historical context (if applicable) and theoretical foundations (if applicable).
    *   Include mathematical derivations (as appropriate) using LaTeX (e.g., `$E=mc^2$`) if mathematical derivations are appropriate for the content.  Don't force equations into the document. Define all terms clearly. Explain reasoning behind steps.
    *   Use Mermaid diagrams (in appropriate ```{{mermaid}} ... ``` blocks) or other visualizations if they aid understanding.
    *   Link to 1-3+ high-quality external resources (papers, articles, tutorials) for further reading if possible. Include DOI links or other URLs (if known).  This should be done with quarto inline references.
    *  Add a References section.  Use APA format for references. Include .bib entries in the document for all references.
3.  **Practical Example / Code Implementation**:
    *   Provide a working code example in {examples_programming_language} if appropriate.  Do not force code into the document if not appropriate.
    *   Use the libraries mentioned ({libraries_used}) or other suitable ones if generating code.
    *   Ensure any generated code is well-commented and explains the implementation steps.
    *   Python code should use typing for all variables and method signatures.
    *   If data is needed for an illustrative example, either generate synthetic data or use a small, easily accessible public dataset (provide loading code).  Exercises can use the above datasets.
4.  **Student Exercise**:
    *   Design a small homework problem related to the topic. It could be conceptual, require modifying the example code, or involve applying the concept to a new scenario.  The homework should take 30-60 minutes to complete.
5.  **Exercise Solution**:
    *   Provide a clear solution or key steps for the student exercise.
6.  **Quiz**:
    *   Create a ten question quiz (multiple-choice or true/false) covering key aspects of the topic.  The questions should be answerable using the content of the output document.
    *   Make the answers viewable directly after the question when the user clicks to unfold the answer.
    *   Create a concise answer key in a hidden section right after the quiz.

General Formatting Notes:
*   Ensure code blocks are correctly specified for {examples_programming_language}.
*   Be verbose and pedagogical throughout.
*   All code blocks should be folded by default.
*   Use emoji to spice up the document
*   IMPORTANT: When creating markdown lists of any kind, always have a blank line right before the list starts so it will render correctly in quarto.
*   IMPORTANT: All code markdown sections should be folded (closed) by default
*   IMPORTANT: The yaml header should only include title, subtitle and bibliography settings.  The bibliography file name should be the same as the Quarto document name but with .bib. The bibliography filename should be commented out by default.
*   IMPORTANT: The document should start at the ## heading level (2 has marks)
"""


def render():
    st.subheader("Course Settings")

    st.session_state.course_title = st.text_input(
        "Course Title",
        value=st.session_state.get('course_title', "Generative AI"),
    )
    st.session_state.course_description = st.text_area(
        "Course Description",
        value=st.session_state.get('course_description', "Covering both theoretical design and practical application of generative AI."),
    )
    st.session_state.lecture_level = st.text_input(
        "Module Level",
        value=st.session_state.get('lecture_level', "graduate"),
    )
    st.session_state.examples_programming_language = st.selectbox(
        "Example Programming Language",
        ["Python", "R"],
        index=["Python", "R"].index(st.session_state.get('examples_programming_language', 'Python')),
    )
    st.session_state.libraries_used = st.text_input(
        "Libraries to Use (Informational)",
        value=st.session_state.get('libraries_used', "Use appropriate libraries like scikit-learn, PyTorch, TensorFlow, tidyverse, etc."),
    )

    NOTEBOOK_TYPES = ["Quarto notebook", "Jupyter notebook", "PowerPoint (pptx)"]
    st.session_state.notebook_type = st.selectbox(
        "Output type",
        NOTEBOOK_TYPES,
        index=NOTEBOOK_TYPES.index(st.session_state.get('notebook_type', 'Quarto notebook')),
    )

    if st.session_state.notebook_type == "Jupyter notebook":
        extension = "ipynb"
    elif st.session_state.notebook_type == "PowerPoint (pptx)":
        extension = "pptx"
    else:
        extension = "qmd"
    st.session_state.extension = extension

    col1, col2 = st.columns(2)
    with col1:
        st.session_state.num_lectures = st.number_input(
            "Number of Modules to Generate",
            value=st.session_state.get('num_lectures', 10), min_value=1, max_value=50,
        )
    with col2:
        st.session_state.lecture_length = st.number_input(
            "Approx. Module Length (minutes)",
            value=st.session_state.get('lecture_length', 60), min_value=10, max_value=180,
        )

    st.subheader("Output Instructions Template")
    st.session_state.instructions = st.text_area(
        "Output Instructions (can use placeholders like {course_title}, {examples_programming_language}, {libraries_used})",
        value=st.session_state.get('instructions', DEFAULT_INSTRUCTIONS),
        height=450,
    )

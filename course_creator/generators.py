import json
import re
from typing import Optional

import nbformat
import streamlit as st


def generate_lectures(chatbot, course_title, course_description, num_lectures, lecture_length, level="graduate", rag_context: Optional[str] = None):
    if not chatbot:
        return None

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
    response = None
    try:
        response = chatbot.completeAsJSON(prompt, context=rag_context)
        parsed_response = json.loads(response)

        if not isinstance(parsed_response, list):
            raise ValueError("Expected a JSON array as the root object.")

        if not all(
            isinstance(lecture, dict) and
            "title" in lecture and isinstance(lecture["title"], str) and
            "description" in lecture and isinstance(lecture["description"], str) and
            len(lecture.keys()) == 2
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
        st.error(f"An unexpected error occurred during module generation: {e}")
        st.text("Raw response received (if available):")
        st.code(response or "Response not captured.", language='text')
        return None


def generate_topics(chatbot, lectures, min_topics, max_topics, rag_context: Optional[str] = None):
    if not chatbot:
        return []

    all_lecture_topics = []
    total_lectures = len(lectures)

    progress_bar = st.progress(0)
    status_text = st.empty()

    for i, lecture in enumerate(lectures):
        status_text.text(f"Generating topics for module {i+1}/{total_lectures}: {lecture['title']}...")
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
        response = None
        try:
            response = chatbot.completeAsJSON(prompt, context=rag_context)
            parsed_response = json.loads(response)

            if not isinstance(parsed_response, list):
                raise ValueError("Expected a JSON array as the root object.")

            if not all(
                isinstance(topic, dict) and
                "title" in topic and isinstance(topic["title"], str) and
                "description" in topic and isinstance(topic["description"], str) and
                len(topic.keys()) == 2
                for topic in parsed_response
            ):
                raise ValueError(
                    "Each element in the array must be an object with exactly 'title' (string) and 'description' (string) keys."
                )

            lecture_topics = parsed_response

        except (json.JSONDecodeError, ValueError) as e:
            st.error(f"Error processing topics for module '{lecture['title']}': {e}. Skipping this module.")
            st.text("Raw response received:")
            st.code(response or "Response not captured.", language='text')
            lecture_topics = []
        except Exception as e:
            st.error(f"An unexpected error occurred generating topics for module '{lecture['title']}': {e}. Skipping.")
            st.text("Raw response received (if available):")
            st.code(response or "Response not captured.", language='text')
            lecture_topics = []

        all_lecture_topics.append(lecture_topics)
        progress_bar.progress((i + 1) / total_lectures)

    status_text.text("Topic generation complete.")
    progress_bar.empty()
    return all_lecture_topics


def create_notebook(
    chatbot,
    lecture_title,
    topic_title,
    topic_description,
    full_topics_list_json,
    instructions_template,
    course_title,
    examples_programming_language,
    notebook_type,
    libraries_used,
    rag_context: Optional[str] = None,
):
    if not chatbot:
        return None

    instructions = instructions_template.format(
        course_title=course_title,
        examples_programming_language=examples_programming_language,
        libraries_used=libraries_used,
    )

    lib_install_req = ""
    if examples_programming_language == "Python":
        lib_install_req = "%pip install -q <library-name>"
    elif examples_programming_language == "R":
        lib_install_req = "install.packages(\"<library-name>\")"

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
        completion_method = lambda p: chatbot.completeAsJSON(p, context=rag_context)
    elif notebook_type == "Quarto notebook":
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
        completion_method = lambda p: chatbot.complete(p, context=rag_context)
    else:  # PowerPoint (pptx) — Quarto presentation format
        instructions = """
        Create concise, presentation-ready slide content for a single topic. Follow these guidelines:

        Slide Structure:
        - Use ## headings to define individual slides (each ## creates a new slide)
        - Each slide should cover ONE focused idea — do not overload slides with text
        - Aim for 3–6 bullet points per slide maximum; prefer short, punchy phrases over full sentences
        - Use ::: incremental ::: blocks for step-by-step builds where appropriate

        Required Slides (in order):
        1. Title slide (automatically generated from YAML)
        2. ## Overview — 3–5 bullet points summarising the topic
        3. ## Background & Theory — split into multiple slides as needed; each covering one concept
        4. ## Key Formula / Algorithm (if applicable) — one LaTeX equation per slide with a one-line explanation
        5. ## Practical Example — describe the example setup briefly; code snippet or pseudocode on its own slide
        6. ## Summary — 3–5 takeaway bullets
        7. ## Quiz — 3–5 knowledge-check questions, one per slide, with the answer in speaker notes

        Formatting Rules:
        - Speaker notes go in ::: {.notes} ... ::: blocks under each slide — use these for elaboration
        - Do NOT write paragraphs of prose on slides; save explanations for speaker notes
        - Use emoji sparingly to highlight key points (one per slide maximum)
        - Tables and Mermaid diagrams are welcome on their own slides when they add clarity
        - All code blocks should be fenced with the appropriate language tag
        """
        output_requirements = f"""
        Output:
        - Produce a valid Quarto presentation file (.qmd) with `format: pptx` in the YAML header.
        - The YAML header must be exactly:
          ---
          title: "<topic title>"
          subtitle: "<module title>"
          format: pptx
          ---
        - The entire response MUST be ONLY the raw text content of the .qmd file.
        - Do NOT include ```qmd ... ``` or ```markdown ... ``` markers or any text before or after the .qmd content.
        - Separate slides using ## headings (second-level headings create new slides in Quarto/Pandoc).
        - Keep slide content concise — 3–6 bullet points per slide; no paragraphs of prose on slides.
        - Use ::: {{.notes}} ... ::: blocks for speaker notes where elaboration is needed.
        - Use standard Markdown bullet points (- item) for lists on slides.
        - Code examples should use fenced code blocks with the language tag (e.g., ```python ... ```).
        - Mathematical equations should use LaTeX inline ($...$) or display ($$...$$) notation.
        - IMPORTANT: When creating markdown lists, always have a blank line before the list starts.
        """
        completion_method = lambda p: chatbot.complete(p, context=rag_context)

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

        if notebook_type in ("Quarto notebook", "PowerPoint (pptx)"):
            response = chatbot.extract_markdown_content(response, "qmd")
            if response.startswith("```") and response.endswith("```"):
                st.warning(f"LLM response for {topic_title} might be incorrectly wrapped in code fences. Attempting to clean.")
                response = re.sub(r'^```[a-zA-Z]*\n?', '', response)
                response = re.sub(r'\n?```$', '', response)

        if notebook_type == "Jupyter notebook":
            try:
                nb_data = json.loads(response)
                if not isinstance(nb_data, dict) or "cells" not in nb_data or "metadata" not in nb_data:
                    raise ValueError("Response is valid JSON but doesn't look like a Jupyter notebook.")
                nbformat.validate(nb_data)
            except (json.JSONDecodeError, ValueError, nbformat.ValidationError) as json_val_err:
                st.error(f"LLM response for {topic_title} was not valid Jupyter Notebook JSON: {json_val_err}")
                st.text("Raw response received:")
                st.code(response, language='text')
                return None

        return response

    except Exception as e:
        st.error(f"An unexpected error occurred during notebook generation for '{topic_title}': {e}")
        return None

I want to create a grading system in streamlit using anaconda env ai_grader.  You will need to create the environment.

I will hand out quizzes or exams on paper.  The students will write their names at the top in a `Name:` section.

I will scan all the evals into a single PDF (vector format).  There can be 1..n pages per eval (don't assume single page).

The app will use .env for the LLM.  The OPENAI_VISION_MODEL model should be used for vision only.

OPENAI_ENDPOINT=https://llm-api.arc.vt.edu/api/v1
OPENAI_APIKEY=sk-xxxxxxxxxxxxxxxxx
OPENAI_MODEL=thinkinglatest
OPENAI_VISION_MODEL=vision

The UI should have a config tab with the following sections:

Note: This entire UI should be backed by a single .JSON file stored in the Working dir that can be saved/loaded at anytime.

- ARC API Key (optional override of .env setting)
- Working dir (you create this dir in /tmp but display and edit it with a "Load" button)
- Quiz/Exam Name
- Class roster (add docs that say it is a single-column .CSV of student names, one per line, each written "last_name, first_name" in double quotes; a `name` header row is optional). Names read off the scans by the vision model are fuzzy-matched to the roster — never assume an exact match.
- Exam upload (single file PDF only)
- Rubric & answers upload (single file PDF only)
- Grounding materials (multiple PDF upload) 
- Max points (default to 100; required)
- Min points (default to 0; required)
- Curve: Min Average Score (default to 90; optional)
- Additional instructions on grading to the LLM (freeform text; optional that defaults to "Allow some deviation from the rubric on short-answer if the answer fits the course material.  Add constructive comments that reward critical thinking skills.")

There should be a Reset button to start over.  It has a confirmation.

There should be a button to Perform OCR that splits the quizzes and populates a grid on a second tab.  The student name should be a drop-down list `first_name last_name` that can be overridden.  The structured markdown (from OCR) should be presented in a folded section. If the OCR could not find the exact student, leave it blank.  Make this field required.  There should be a global "Save" button that saves the outputs to the .JSON file.

On the next grading tab, there should be a button that says "Grade" that has the LLM grade each exam in a clean context window using the additional instructions (echo those on the tab in a read-only input field).  The base constitutional prompt should be appropriate for a grading tool that includes the relevant source materials as grounding + rubric/answers.  The LLM should be instructed to stick to the supplied materials and rubric (CRITICAL).  Make sure the LLM grading is resilient.


Once grading is finished, the system should apply the curve.  If the actual min avg score <  min average score, add the required points to each deduction to meet the new average.  Don't have fractions.  Use ceil or floor to make integers.  Then finally, apply the min/max point ranges as needed. Grading should include having the LLM comments incorporated into the output (in red) and the final score.

On the last tab, have a "Download" function that downloads all the rendered PDFs graded with the student name `last_name_first_name.pdf` as the filename.

There are sample inputs in /home/smutchle/Work/data_science/vt/ai_grader/example for testing.

If you have questions, ask now.
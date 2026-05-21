"""Spec Suggester agent — drafts a CourseSpec from uploaded materials.

Reads raw materials text and returns a CourseSpec draft (MLOs + Topics +
guiding principles). Pre-fills the Run page form so the instructor reviews
+ edits instead of typing from scratch.

Lives under the standard agent framework so retry-on-validation + sidecar
audit trail come for free.
"""

from typing import ClassVar

from models import CourseSpec

from agents.base import BaseAgent


class SpecSuggesterAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "spec_suggester"

    def suggest(self, materials_text: str) -> CourseSpec:
        prompt = (
            "Draft a CourseSpec for the materials below. Follow the "
            "conventions in your constitution — 5–10 MLOs at honest Bloom "
            "levels, 5–10 weighted topics, a short guiding-principles "
            "paragraph. Output a single CourseSpec JSON.\n\n"
            "--- MATERIALS ---\n"
            f"{materials_text}"
        )
        return self._invoke(prompt, CourseSpec)

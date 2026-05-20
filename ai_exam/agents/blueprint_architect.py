"""Blueprint Architect agent.

Builds the topic-by-Bloom-level matrix that governs coverage before any items
are written. Reconciles three inputs: the course specification (planned CLOs,
topics, topic weights), the exam specification (item-type counts, point budget,
difficulty distribution), and the themes the SME extracted from the actual
uploaded materials.
"""

from typing import ClassVar

from models import (
    Blueprint,
    CourseSpec,
    ExamSpec,
    Theme,
)

from agents.base import BaseAgent


def _format_themes(themes: list[Theme]) -> str:
    if not themes:
        return "(no themes provided)"
    return "\n".join(
        f"- rank={t.rank} | id={t.id}\n  {t.text}\n  ({len(t.source_refs)} source chunks)"
        for t in themes
    )


class BlueprintArchitectAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "blueprint_architect"

    def propose_blueprint(
        self,
        course_spec: CourseSpec,
        exam_spec: ExamSpec,
        themes: list[Theme],
    ) -> Blueprint:
        prompt = (
            "Propose a blueprint for this exam: a list of (topic, Bloom level) cells "
            "with target item counts and point values. The blueprint is the single "
            "artifact that governs coverage; downstream agents propose and critique "
            "items against it.\n\n"
            "Reconcile three inputs:\n"
            "  - The course spec: planned CLOs, topics with relative weights, and "
            "guiding principles.\n"
            "  - The exam spec: total points, time budget, item-type counts, target "
            "difficulty distribution, accommodations.\n"
            "  - The themes the SME extracted from the actual uploaded materials: "
            "what the materials actually cover, ranked by centrality.\n\n"
            "Constraints the blueprint must satisfy:\n"
            "  - Sum of cell target_points equals exam_spec.total_points.\n"
            "  - Sum of cell target_item_counts equals the total of "
            "exam_spec.item_type_counts.\n"
            "  - Every CLO in course_spec appears in at least one cell's clo_refs.\n"
            "  - Topic weighting across cells respects course_spec.topics weights "
            "(treat weights as the planned distribution; deviate only when themes "
            "show the materials cannot support that distribution, and record the "
            "deviation in coverage_check.warnings).\n"
            "  - Bloom distribution reflects the level of the CLOs (do not let cells "
            "drift toward REMEMBER and UNDERSTAND).\n"
            "  - Include a coverage_check with the actual covered/uncovered lists "
            "and any warnings.\n\n"
            f"--- COURSE SPEC ---\n{course_spec.model_dump_json(indent=2)}\n\n"
            f"--- EXAM SPEC ---\n{exam_spec.model_dump_json(indent=2)}\n\n"
            f"--- SME THEMES (ranked) ---\n{_format_themes(themes)}"
        )
        return self._invoke(prompt, Blueprint)

    def revise_blueprint(
        self,
        blueprint: Blueprint,
        course_spec: CourseSpec,
        exam_spec: ExamSpec,
        faculty_feedback: str,
    ) -> Blueprint:
        """Revise a blueprint after Checkpoint 1 faculty review.

        Faculty edits may be inline cell changes in the UI, or free-text feedback
        captured into faculty_feedback. The agent's job is to reconcile the
        feedback with the spec constraints — do not silently drop a constraint
        to satisfy a feedback line; flag the conflict in coverage_check.warnings.
        """
        prompt = (
            "Revise this blueprint in response to faculty feedback. Preserve the "
            "constraints listed in your constitution (point total, item count total, "
            "CLO coverage, topic weighting, Bloom distribution). If the feedback "
            "directly conflicts with a constraint, do not silently drop the "
            "constraint — apply the feedback to the extent possible and record the "
            "conflict in coverage_check.warnings so the next checkpoint surfaces it.\n\n"
            f"--- CURRENT BLUEPRINT ---\n{blueprint.model_dump_json(indent=2)}\n\n"
            f"--- COURSE SPEC ---\n{course_spec.model_dump_json(indent=2)}\n\n"
            f"--- EXAM SPEC ---\n{exam_spec.model_dump_json(indent=2)}\n\n"
            f"--- FACULTY FEEDBACK ---\n{faculty_feedback}"
        )
        return self._invoke(prompt, Blueprint)

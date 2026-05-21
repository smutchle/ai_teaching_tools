"""Narrator agent — polishes a structured run timeline into flowing prose.

Reused via `BaseAgent` so it inherits retry-on-validation, event logging,
and provider routing. The structured draft is produced by the templater
in `narrative/templater.py`; this agent takes the draft as input and
returns a polished markdown narrative.
"""

from typing import ClassVar

from models import Narrative

from agents.base import BaseAgent


class NarratorAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "narrator"

    def polish(self, structured_draft: str) -> str:
        """Rewrite the structured timeline as flowing markdown prose."""
        prompt = (
            "Rewrite the structured run timeline below as flowing markdown "
            "prose, following the conventions in your constitution (voice, "
            "structure, faithfulness, length). Preserve every numeric fact "
            "and decision — do not summarize away counts or invent new "
            "events. Output only the markdown narrative, no preamble.\n\n"
            "--- STRUCTURED TIMELINE ---\n"
            f"{structured_draft}"
        )
        return self._invoke(prompt, Narrative).narrative

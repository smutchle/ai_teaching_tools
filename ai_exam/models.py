"""Pydantic contracts between agents.

Models here are the typed interface that the BaseAgent serializes to/from Claude
via forced tool use. Order matters: types referenced by `list[...]` or as field
types must be defined before the models that use them, so Pydantic can resolve
them at schema-generation time without forward-ref rebuilds.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ItemType(str, Enum):
    MCQ = "mcq"
    SHORT_ANSWER = "short_answer"
    PROBLEM = "problem"
    DERIVATION = "derivation"
    DATA_INTERP = "data_interp"


class BloomLevel(str, Enum):
    REMEMBER = "remember"
    UNDERSTAND = "understand"
    APPLY = "apply"
    ANALYZE = "analyze"
    EVALUATE = "evaluate"
    CREATE = "create"


class KnowledgeType(str, Enum):
    FACTUAL = "factual"
    CONCEPTUAL = "conceptual"
    PROCEDURAL = "procedural"
    METACOGNITIVE = "metacognitive"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ObjectionSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RebuttalStance(str, Enum):
    ACCEPT = "accept"
    REBUT = "rebut"
    DEFER = "defer"


class ItemStatus(str, Enum):
    DRAFT = "draft"
    REFINED = "refined"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ExamType(str, Enum):
    MIDTERM = "midterm"
    FINAL = "final"
    QUIZ = "quiz"
    QUALIFYING = "qualifying"


class AccommodationKind(str, Enum):
    EXTENDED_TIME = "extended_time"
    SCREEN_READER = "screen_reader"
    LARGE_PRINT = "large_print"


class Chunk(BaseModel):
    id: str
    text: str
    source_doc: str
    locator: str | None = None
    is_prior_exam: bool = False


class SourceRef(BaseModel):
    chunk_id: str
    source_doc: str
    locator: str | None = None


class CLO(BaseModel):
    """A course learning outcome with explicit cognitive level."""

    id: str
    text: str
    bloom_level: BloomLevel
    knowledge_type: KnowledgeType


class Topic(BaseModel):
    id: str
    name: str
    weight: float = Field(description="Relative weight; topic weights sum to 1.0")
    source_refs: list[SourceRef] = Field(default_factory=list)


class CourseSpec(BaseModel):
    clos: list[CLO]
    topics: list[Topic]
    guiding_principles: str


class ItemTypeCounts(BaseModel):
    """Item counts per item type. Modeled as named fields rather than a dict
    so the JSON schema sent to Claude has explicit, type-checked keys."""

    mcq: int = 0
    short_answer: int = 0
    problem: int = 0
    derivation: int = 0
    data_interp: int = 0


class DifficultyDistribution(BaseModel):
    """Target fraction of items at each difficulty. Ratios should sum to 1.0."""

    easy_ratio: float
    medium_ratio: float
    hard_ratio: float


class ExamSpec(BaseModel):
    exam_type: ExamType
    total_points: int
    time_budget_minutes: int
    item_type_counts: ItemTypeCounts
    difficulty_distribution: DifficultyDistribution
    accommodations_required: list[AccommodationKind] = Field(default_factory=list)
    latex_required: bool = True
    figure_support: bool = True


class Theme(BaseModel):
    id: str
    text: str
    rank: int = Field(description="1 = most central to the course")
    rationale: str
    source_refs: list[SourceRef]


class ThemeList(BaseModel):
    # Default to [] so a model that returns an empty/missing field yields
    # an empty list instead of crashing with "Field required". Phase 1
    # under-fills (zero themes) — the BlueprintArchitect handles that case.
    themes: list[Theme] = Field(default_factory=list)


class BlueprintCell(BaseModel):
    topic_id: str
    topic_name: str
    bloom_level: BloomLevel
    target_item_count: int
    target_points: int
    clo_refs: list[str] = Field(default_factory=list)


class CoverageCheck(BaseModel):
    """Validation of a blueprint against the course and exam specs."""

    clos_covered: list[str]
    clos_uncovered: list[str]
    topics_covered: list[str]
    topics_uncovered: list[str]
    point_total: int
    target_point_total: int
    item_total: int
    warnings: list[str] = Field(default_factory=list)


class Blueprint(BaseModel):
    cells: list[BlueprintCell]
    coverage_check: CoverageCheck


class ProvenanceEvent(BaseModel):
    epoch: int
    agent: str
    action: Literal["proposed", "edited", "objected", "accepted", "rejected", "resolved"]
    target: str
    diff: str | None = None
    rationale: str
    timestamp: datetime


class ItemDraft(BaseModel):
    """An item as proposed or edited by the SME, before Moderator promotion."""

    type: ItemType
    stem: str
    options: list[str] | None = None
    answer_key: str
    rubric: str | None = None
    points: int
    bloom_level: BloomLevel
    knowledge_type: KnowledgeType
    clo_refs: list[str]
    topic_refs: list[str]
    source_refs: list[SourceRef]
    difficulty_est: Difficulty
    accessibility_notes: list[str] = Field(default_factory=list)


class ItemDraftList(BaseModel):
    # Default to [] so an empty/missing field yields an empty list instead
    # of crashing the whole Phase-2 gather. A cell that gets no drafts
    # under-fills; the rest of Phase 2 proceeds normally.
    items: list[ItemDraft] = Field(default_factory=list)


class Item(ItemDraft):
    """An item after Moderator promotion: assigned id, status, provenance, and
    optional psychometric fields populated downstream."""

    id: str
    status: ItemStatus = ItemStatus.DRAFT
    discrimination_est: float | None = None
    provenance: list[ProvenanceEvent] = Field(default_factory=list)


class ObjectionDraft(BaseModel):
    """An objection as produced by a critic agent, before Moderator promotion.

    Critics own severity, category, target, claim, and suggested_fix. The
    Moderator assigns id (for cross-referencing in rebuttals and provenance)
    and stamps agent (drawn from the critic's PERSONA_NAME).
    """

    severity: ObjectionSeverity
    category: str
    target: str = Field(description="item_id, 'blueprint', or 'exam_global'")
    claim: str
    suggested_fix: str | None = None


class ObjectionDraftList(BaseModel):
    objections: list[ObjectionDraft] = Field(default_factory=list)


class Objection(ObjectionDraft):
    id: str
    agent: str


class ItemObjections(BaseModel):
    """One critic's objections for a single item, used in batched critique.

    item_id ties the inner objections back to the item under review. Inside
    `objections`, each ObjectionDraft's `target` should also equal item_id —
    the Moderator patches any mismatches before promotion.
    """

    item_id: str
    objections: list[ObjectionDraft] = Field(default_factory=list)


class ItemObjectionsBatch(BaseModel):
    """Wrapper for a critic's response over multiple items in one call.

    The critic returns exactly one ItemObjections entry per input item, even
    when it has no concerns (objections=[]). The wrapper exists so the JSON
    schema enforces a list-of-entries rather than a free-form mapping.
    """

    items: list[ItemObjections] = Field(default_factory=list)


class Rebuttal(BaseModel):
    objection_id: str
    stance: RebuttalStance
    rationale: str = Field(
        default="",
        description=(
            "Brief justification for the stance. Optional — omit when the "
            "stance is self-explanatory; never omit `objection_id` or "
            "`stance`."
        ),
    )
    proposed_edit_summary: str | None = Field(
        default=None,
        description="Summary of the edit you would make. Required when stance == accept.",
    )


class RebuttalBatch(BaseModel):
    """SME's stance on every non-critical objection raised against one item.

    One Rebuttal per objection; objection_id ties each Rebuttal back to the
    objection it answers. The batched form lets SME consider overlapping or
    conflicting objections together rather than rebutting each in isolation,
    and collapses one Claude/Kimi call per objection to one per item.
    """

    rebuttals: list[Rebuttal] = Field(default_factory=list)


class EditResult(BaseModel):
    updated_draft: ItemDraft
    rationale: str = Field(
        default="",
        description=(
            "Brief explanation of what was changed and why. "
            "Optional — omit when the change is self-evident or you have no "
            "additional commentary; never omit `updated_draft`."
        ),
    )


class AlignmentResult(BaseModel):
    """LOA's verification verdict for one item.

    is_aligned is true only when the actual Bloom level matches the item's
    claimed bloom_level AND the actual CLOs include at least one of the item's
    claimed clo_refs.
    """

    is_aligned: bool
    actual_bloom_level: BloomLevel
    actual_clo_refs: list[str]
    notes: str


class RealignmentSuggestion(BaseModel):
    """LOA's recommendation for fixing a misaligned item."""

    diagnosis: str
    suggested_action: Literal["edit_item", "remap_clos", "remap_bloom", "reject"]
    proposed_clo_refs: list[str] | None = None
    proposed_bloom_level: BloomLevel | None = None
    edit_summary: str | None = None
    rationale: str


class DifficultyEstimate(BaseModel):
    """Psychometrician's per-item difficulty estimate.

    confidence is the agent's self-estimated reliability of the difficulty
    judgment (0.0 = guess, 1.0 = high certainty). Used by the Moderator to
    weight psychometric flags when items disagree with their cell's target
    difficulty.
    """

    difficulty: Difficulty
    confidence: float
    rationale: str


class BloomDistributionStat(BaseModel):
    """Per-Bloom-level count and point totals across a draft exam."""

    bloom_level: BloomLevel
    item_count: int
    points: int


class CLOCoverage(BaseModel):
    clo_id: str
    item_count: int
    points: int
    is_covered: bool


class DifficultyCurve(BaseModel):
    easy_count: int
    medium_count: int
    hard_count: int
    target_easy_ratio: float
    target_medium_ratio: float
    target_hard_ratio: float


class ExamReport(BaseModel):
    """Exam-level psychometric and coverage snapshot."""

    bloom_distribution: list[BloomDistributionStat]
    difficulty_curve: DifficultyCurve
    clo_coverage: list[CLOCoverage]
    imbalance_notes: list[str]
    summary: str


class ExamAudit(BaseModel):
    """Psychometrician's exam-level audit: report + any objections raised."""

    report: ExamReport
    objections: list[ObjectionDraft] = Field(default_factory=list)


class ExamDraft(BaseModel):
    """The durable exam-design state passed between phases.

    Per the design doc §2, this is the single object that holds items, blueprint,
    and the open/resolved objection lists across the epoch loop. The Moderator
    is the only writer; agents receive slices and return typed results that the
    Moderator merges back in.
    """

    items: list[Item]
    blueprint: Blueprint
    objections_open: list[Objection] = Field(default_factory=list)
    objections_resolved: list[Objection] = Field(default_factory=list)
    epoch: int = 0


class ItemVariant(BaseModel):
    """An accommodation-specific variant of a base item."""

    base_item_id: str
    kind: AccommodationKind
    item: ItemDraft
    adaptation_notes: str


class GroundingResult(BaseModel):
    """Grounding Verifier's verdict on whether an item's answer is supported
    by the chunks the SME cited as evidence.

    is_grounded is true only when the cited chunks provide sufficient evidence
    for the item's answer_key. missing_evidence names specific claims in the
    answer that the chunks do not support; supported_claims names claims they
    do. Failures route back to SME via Moderator.
    """

    is_grounded: bool
    diagnosis: str
    supported_claims: list[str]
    missing_evidence: list[str]


class SolveAttempt(BaseModel):
    """Adversarial Student's attempt to answer an item using only test-wiseness.

    Critical: the adversary never sees answer_key, rubric, source_refs, or any
    field that would leak the answer. confidence reflects how strongly the
    test-wiseness signal points at the chosen answer; exploit_used names the
    specific item-writing flaw or guessing strategy the adversary relied on.
    """

    target: str
    chosen_answer: str
    confidence: float
    exploit_used: str
    notes: str

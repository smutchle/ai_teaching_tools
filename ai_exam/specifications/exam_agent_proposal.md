# A Multi-Agent System for Collaborative Exam Design in the Sciences

## An Internal Proposal

---

## Summary

This proposal describes a software system that supports faculty in the College of Science in designing high-quality exams for undergraduate and graduate courses. The system is built around a deliberate pedagogical claim: that good exam design is fundamentally a deliberative activity, requiring the simultaneous consideration of content mastery, cognitive demand, accessibility, and measurement quality, and that these considerations are best surfaced through structured disagreement rather than checklist compliance. The system implements this claim computationally, using a team of specialized AI agents that propose, critique, and refine items across multiple rounds, with the instructor of record retained as the authoritative decision-maker at defined checkpoints.

The proposal is intended for departmental colleagues considering whether such a tool merits adoption, piloting, and further development.

---

## Motivation

Exam design in research-intensive science departments is chronically under-resourced relative to its consequences. A typical instructor authoring a midterm faces a combinatorial problem: the exam must cover the right topics in the right proportions, target the right cognitive levels, distinguish students who have understood the material from those who have not, remain accessible to students with diverse needs and accommodations, and do all of this within the time and point budget available. Each of these dimensions is the subject of an established educational literature, but few faculty have the time to consult that literature for each assessment they write.

The most common failure modes are well-documented. Exams drift toward the cognitive levels that are easiest to write items for — usually recall and procedural application — even when the course outcomes promise analysis, evaluation, or modeling. Item-writing flaws such as grammatical cueing in multiple-choice stems, implausible distractors, and overlapping options inflate scores without measuring anything. Construct-irrelevant difficulty, where the language of an item is harder than the construct it is meant to assess, disproportionately penalizes students who are still developing English fluency or who read more slowly for reasons unrelated to the discipline. Accessibility accommodations are often produced reactively, as ad hoc modifications to a finished exam, rather than designed in from the start.

These failure modes are not the result of inattention. They are the result of a single author trying to hold every dimension in mind at once, on a deadline, without a structured process for surfacing the trade-offs.

## Theoretical Foundation

The system is anchored in three established frameworks from educational measurement and instructional design, used in combination rather than in isolation.

**Backward design** (Wiggins & McTighe, *Understanding by Design*) supplies the overall logic. Faculty begin by specifying the enduring understandings and course learning outcomes that the exam is meant to provide evidence about, then determine what evidence would warrant inferences about student attainment of those outcomes, and only then turn to the writing of individual items. The system enforces this ordering by requiring a course specification and an exam blueprint to be approved before any items are generated. This is a structural constraint, not a recommendation: the agent that proposes items is forbidden from operating before the blueprint is approved.

**Bloom's revised taxonomy** (Anderson & Krathwohl) supplies the cognitive vocabulary. The blueprint is a two-dimensional matrix with course topics on one axis and cognitive process levels — remember, understand, apply, analyze, evaluate, create — on the other, with target item counts and point values in each cell. This matrix is the central artifact of the design process. It makes coverage decisions explicit, it prevents the silent drift toward lower cognitive levels described above, and it gives every downstream agent a concrete target to check items against.

**Constructive alignment** (Biggs) supplies the per-item discipline. Each item carries an explicit reference to one or more course learning outcomes, and a dedicated agent in the system verifies that the cognitive demand of the item matches the cognitive level at which the referenced outcome is stated. An outcome that promises "students will be able to *analyze* enzyme kinetics data" is not satisfied by an item that asks students to *recall* the Michaelis-Menten equation. Misaligned items are flagged for revision or rejection. This is the operational core of what makes the system more than a generator of plausible-looking questions.

Underlying all of this, and invoked more lightly, is the logic of **evidence-centered assessment design** (Mislevy). ECD treats every assessment as an argument: a claim about what a student knows or can do, evidence that would warrant that claim, and a task designed to elicit that evidence. The system makes this argument structure explicit by attaching to every item a record of which outcome it claims to provide evidence for, which source material grounds the content, which cognitive level it targets, and which design decisions were made about it during refinement. This record is what allows an instructor to defend an exam when a student appeals a grade. It is also what allows the department to audit and improve the design process over time.

## The System: An Overview

The system is presented to faculty as a web application, but its substance is a team of specialized agents that propose, critique, and refine exam items under instructor supervision. The cast is small and the division of labor deliberate:

| Agent | Role |
|---|---|
| **Subject Matter Expert** | Extracts themes from uploaded materials, proposes items grounded in specific passages, defends or revises items in response to critique |
| **Blueprint Architect** | Builds the topic-by-Bloom-level matrix that governs coverage before any items are written |
| **Learning Outcomes Alignment** | Verifies that each item targets the cognitive level promised by the course outcome it claims to assess |
| **Item-Writing Specialist** | Catches mechanical flaws: grammatical cueing, implausible distractors, absolute-language giveaways, and similar defects from the assessment literature |
| **Accessibility Expert** | Reviews items for readability and construct-irrelevant difficulty; generates exam variants for accommodations |
| **Psychometrician** | Estimates difficulty and discrimination, audits the distribution across the exam, flags imbalances |
| **Adversarial Student** | Attempts to answer each item using only test-wiseness — no access to materials or answer key — to surface items that reward guessing |
| **Moderator** | Deterministic orchestrator; collects objections, applies the instructor's trade-off policy, escalates unresolved disagreements |

Faculty interaction is structured around three inputs and three checkpoints. The inputs are the course materials, a course specification (learning outcomes tagged with Bloom levels, weighted topic list, guiding principles), and an exam specification (type, time and point budget, item-type distribution, target difficulty distribution, required accessibility variants). A fourth input deserves particular attention: faculty provide a **trade-off policy**, a ranked priority list over content fidelity, cognitive alignment, accessibility, discrimination, and brevity. This policy is consulted whenever the agents disagree, which they will. Requiring the instructor to set the policy in advance places the responsibility for value judgments where it belongs and prevents the system from defaulting to whichever dimension happens to be most easily quantified.

The three checkpoints — blueprint approval, item bank review, and final draft review — gate the system's progression. The agents cannot move past a checkpoint without instructor sign-off, and the instructor can intervene at any point to override an agent decision.

Between these checkpoints, the system runs in four phases. **Blueprinting** produces the topic-by-Bloom matrix that governs coverage, built by the Blueprint Architect from themes the SME extracts from the materials. **Item generation** populates each cell of the approved blueprint, with the SME proposing, the Item-Writing Specialist cleaning up mechanics, and the Learning Outcomes Alignment agent verifying constructive alignment; misaligned items are dropped before reaching the instructor. **Refinement** runs multiple epochs of structured critique in parallel — Accessibility Expert, Psychometrician, and Adversarial Student each reviewing every item — with the Moderator collecting objections, routing them to the SME for edit or rebuttal, and applying the trade-off policy when critics disagree. The loop exits when no critical or high-severity objections are raised for a full epoch. **Finalization** produces the exam, answer key, rubrics, accessibility variants, a coverage and difficulty report, and a provenance record — a structured log of which agent proposed each item, which objections were raised, and how each was resolved.

## Why Multiple Agents Rather Than One

A reasonable question is whether the multi-agent structure is necessary. A single, sufficiently capable language model can in principle be prompted to consider content, cognition, accessibility, and measurement all at once. In practice, this approach produces items that look plausible across all dimensions and excel at none. The dimensions trade off against each other, and a single agent asked to optimize all of them simultaneously will reliably produce a smooth compromise that hides where the trade-offs were made.

The multi-agent design makes these trade-offs visible and accountable. When the Accessibility Expert objects that a stem contains construct-irrelevant difficulty, and the SME rebuts that the technical vocabulary in question is precisely the construct being assessed, the disagreement is recorded, the trade-off policy is consulted, and the resolution is logged with rationale. The instructor sees not just the final item but the argument that produced it. This is the educational measurement equivalent of code review, and it works for the same reasons.

A subtler benefit is that the critic agents in the system are explicitly required to produce objections in early epochs, with empty critique rejected and re-prompted. Multi-agent language model systems are prone to convergence through mutual agreement, a failure mode that produces the appearance of deliberation without its substance. Requiring objections at a configurable minimum rate forces the system to surface genuine concerns rather than performing collegiality.

## Faculty Time, Faculty Authority

The system is designed to reduce the time faculty spend on the mechanical aspects of exam construction while increasing the time they spend on the consequential ones. The blueprint approval, the item bank review, and the final draft review are the three points at which faculty judgment is required. Between them, the system handles item drafting, mechanics review, alignment checking, accessibility review, adversarial testing, and the orchestration of objections and edits.

Faculty authority is preserved by construction. The agents cannot modify a blueprint that has not been approved, cannot move past a checkpoint without instructor sign-off, and cannot resolve a disagreement that exceeds the trade-off policy without escalation. The provenance record makes every agent decision auditable, which means that an instructor who suspects the system has made a poor choice can identify exactly where and why, and override it.

## Use Cases Within the College

The system is well-suited to recurring undergraduate exams in courses with stable learning outcomes, where the investment in a careful course specification pays back across semesters. It is also well-suited to graduate qualifying exams, where the audit trail and the explicit construct alignment provide a defensible record of how each item was chosen. It is less well-suited to exams whose pedagogical value lies in unpredictability, such as oral examinations or open-ended research proposals, and no claim is made for those settings.

For science courses specifically, the system supports LaTeX-rendered equations, figure and data-table items, and disciplinary conventions around units, significant figures, and experimental design. The retrieval layer is grounded in the instructor's uploaded materials rather than in the language model's training corpus, which means that an item proposed by the SME must cite a specific passage from the course materials as its source and must pass a grounding check before it enters the item bank.

## Next Steps

If the department finds the proposal worth pursuing, the natural next step is a small pilot: two or three faculty volunteers, one course each, one exam per course, with a structured debrief afterward comparing the system-assisted exam to the instructor's prior practice on the same kind of assessment. The pilot would produce concrete evidence about time savings, item quality, faculty experience, and the points at which the system needs adjustment before broader use.

I am happy to discuss the proposal further, to walk through the technical design with anyone interested, or to identify courses where a pilot would be most informative.

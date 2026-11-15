"""Presentation helpers shared by the Streamlit app and the command-line demo.

They turn the scheduler's objects into friendly text: an emoji for each kind of task and pet,
colour-coded priority and status badges, and table rows. There is no scheduling logic here.

Every emoji used is a single code point that is two terminal cells wide, so `tabulate` tables stay
aligned (emoji that carry an invisible variation selector, such as the warning sign, do not, and
`tests/test_display.py` checks this for every symbol below).
"""

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

from tabulate import tabulate

from pawpal_system import Category, DailyPlan, PetTask, Pet, Priority, Recurrence, Task

if TYPE_CHECKING:  # only for type hints: display.py itself does not depend on the assistant
    from ai_assistant import CheckReport, KBChunk, ProposedTask

CATEGORY_ICONS = {
    Category.WALK: "🐕",
    Category.FEEDING: "🍖",
    Category.MEDS: "💊",
    Category.ENRICHMENT: "🎾",
    Category.GROOMING: "🛁",
    Category.OTHER: "📌",
}
SPECIES_ICONS = {"dog": "🐶", "cat": "🐱"}
DEFAULT_PET_ICON = "🐾"
PET_HEADING = DEFAULT_PET_ICON  # the paw print used as the app's mark

# Colour-coded: red is the most urgent, green the least.
PRIORITY_BADGES = {Priority.HIGH: "🔴 High", Priority.MEDIUM: "🟡 Medium", Priority.LOW: "🟢 Low"}

STATUS_SCHEDULED = "✅ Scheduled"
STATUS_SKIPPED = "⛔ Not scheduled"
STATUS_FLAGGED = "🚨 Required, not scheduled"

CLASH_ICON = "🚧"
REPEAT_ICON = "🔁"
SAVED_ICON = "💾"
SLOT_ICON = "🕒"
DONE_ICON = "✅"
LATER_ICON = "⏳"
OVERDUE_ICON = "❗"

# Everything above, for the test that checks they all line up in a terminal.
ALL_SYMBOLS = (
    list(CATEGORY_ICONS.values()) + list(SPECIES_ICONS.values()) + [DEFAULT_PET_ICON]
    + [badge.split()[0] for badge in PRIORITY_BADGES.values()]
    + [s.split()[0] for s in (STATUS_SCHEDULED, STATUS_SKIPPED, STATUS_FLAGGED)]
    + [CLASH_ICON, REPEAT_ICON, SAVED_ICON, SLOT_ICON, DONE_ICON, LATER_ICON, OVERDUE_ICON]
)


def pet_icon(species: str) -> str:
    """The emoji for a species (a paw print for anything unknown)."""
    return SPECIES_ICONS.get(species, DEFAULT_PET_ICON)


def pet_label(pet: Pet) -> str:
    """A pet's name with its species emoji, e.g. "🐶 Biscuit"."""
    return f"{pet_icon(pet.species)} {pet.name}"


def category_icon(category: Category) -> str:
    """The emoji for a kind of task, e.g. a pill for medication."""
    return CATEGORY_ICONS[category]


def task_label(task: Task) -> str:
    """A task's title with its category emoji, e.g. "💊 Heart medication"."""
    return f"{category_icon(task.category)} {task.title}"


def priority_badge(priority: Priority) -> str:
    """A colour-coded priority, e.g. "🔴 High"."""
    return PRIORITY_BADGES[priority]


def when_text(task: Task) -> str:
    """When a task asks to happen: "fixed 08:00", "prefers 07:30" or "any time"."""
    if task.fixed_time is not None:
        return f"fixed {task.fixed_time:%H:%M}"
    if task.preferred_start is not None:
        return f"prefers {task.preferred_start:%H:%M}"
    return "any time"


def repeats_text(task: Task) -> str:
    """How often a task repeats: "once", or "🔁 daily" / "🔁 weekly"."""
    if task.recurrence is Recurrence.ONCE:
        return "once"
    return f"{REPEAT_ICON} {task.recurrence.value}"


def task_status(task: Task, today: date) -> str:
    """Where a task stands today, with an emoji: done, starts later, overdue, or due today."""
    if task.is_completed():
        return f"{DONE_ICON} done {task.completed_on:%b %d}"
    if task.start_date > today:
        return f"{LATER_ICON} starts {task.start_date:%b %d}"
    if task.start_date < today:
        return f"{OVERDUE_ICON} overdue since {task.start_date:%b %d}"
    return "due today"


def task_status_markdown(task: Task, today: date) -> str:
    """`task_status` coloured for Streamlit markdown: green when done, blue if later, red if overdue."""
    text = task_status(task, today)
    if task.is_completed():
        return f":green[{text}]"
    if task.start_date > today:
        return f":blue[{text}]"
    if task.start_date < today:
        return f":red[{text}]"
    return text


def task_rows(pairs: list[PetTask], today: date) -> list[dict]:
    """One row per task for a table: what, who, when, how often, how important, and its status."""
    return [
        {
            "Task": task_label(pt.task),
            "Pet": pet_label(pt.pet),
            "When": when_text(pt.task),
            "Repeats": repeats_text(pt.task),
            "Priority": priority_badge(pt.task.priority) + (" ⭐" if pt.task.required else ""),
            "Status": task_status(pt.task, today),
        }
        for pt in pairs
    ]


def schedule_rows(plan: DailyPlan) -> list[dict]:
    """The scheduled part of a plan, in time order, as table rows."""
    return [
        {
            "Status": STATUS_SCHEDULED,
            "Time": f"{item.start:%H:%M}–{item.end:%H:%M}",
            "Pet": pet_label(item.pet),
            "Task": task_label(item.task),
            "Min": item.duration(),
            "Priority": priority_badge(item.task.priority),
            "Why": item.reason,
        }
        for item in plan.scheduled_items
    ]


def not_scheduled_rows(plan: DailyPlan) -> list[dict]:
    """The tasks a plan could not schedule, as table rows; a required task is marked in red."""
    return [
        {
            "Status": STATUS_FLAGGED if skipped.flagged else STATUS_SKIPPED,
            "Pet": pet_label(skipped.pet),
            "Task": task_label(skipped.task),
            "Min": skipped.task.duration_minutes,
            "Priority": priority_badge(skipped.task.priority),
            "Why": skipped.reason,
        }
        for skipped in plan.skipped_tasks
    ]


def render_table(rows: list[dict], wrap_column: str = "Why", wrap_width: int = 44) -> str:
    """Rows as a boxed text table for the terminal (`tabulate`), wrapping one long column.

    Columns line up even with emoji because every symbol here is two cells wide. An empty list
    gives "(none)" instead of an empty box."""
    if not rows:
        return "(none)"
    widths = [wrap_width if header == wrap_column else None for header in rows[0]]
    aligns = ["right" if header == "Min" else "left" for header in rows[0]]
    return tabulate(rows, headers="keys", tablefmt="rounded_outline", disable_numparse=True,
                    maxcolwidths=widths, colalign=aligns)


# ---------------------------------------------------------------- Care Assistant proposals
# These work on `ai_assistant.ProposedTask` objects (and the guides and schedule check that came with them).

ASSISTANT_ICON = "✨"
HIGH_CONFIDENCE, MEDIUM_CONFIDENCE = 0.75, 0.5  # at or above: green; at or above: amber; below: red


def confidence_badge(confidence: float) -> str:
    """A colour-coded confidence, e.g. "🟢 92%" (green from 75%, amber from 50%, red below)."""
    icon = "🟢" if confidence >= HIGH_CONFIDENCE else "🟡" if confidence >= MEDIUM_CONFIDENCE else "🔴"
    return f"{icon} {confidence:.0%}"


def proposal_label(p: "ProposedTask") -> str:
    """A proposal's title with its category emoji, e.g. "🐕 Morning walk"."""
    return f"{category_icon(Category(p.category))} {p.title}"


def proposal_when(p: "ProposedTask") -> str:
    """When a proposal asks to happen, worded like `when_text`: "fixed 08:00", "prefers 07:30" or "any time"."""
    if p.time and p.timing == "fixed":
        return f"fixed {p.time}"
    if p.time and p.timing == "preferred":
        return f"prefers {p.time}"
    return "any time"


def proposal_repeats(p: "ProposedTask") -> str:
    """How often a proposal repeats, worded like `repeats_text`."""
    return "once" if p.recurrence == "once" else f"{REPEAT_ICON} {p.recurrence}"


def proposal_priority(p: "ProposedTask") -> str:
    """A proposal's colour-coded priority, with a star when it is marked required."""
    return priority_badge(Priority.from_str(p.priority)) + (" ⭐" if p.required else "")


def proposal_sources(p: "ProposedTask", retrieved: Sequence["KBChunk"]) -> str:
    """The guides a proposal cites, as "Title [id]" joined with "; " (just the id if the guide is unknown)."""
    titles = {chunk.id: chunk.title for chunk in retrieved}
    return "; ".join(f"{titles[s]} [{s}]" if s in titles else f"[{s}]" for s in p.source_ids)


def proposal_fit(p: "ProposedTask", report: "CheckReport | None") -> str:
    """What the trial schedule did with a proposal: "✅ at 07:30", "🕒 moved to 08:00" or "⛔ would not fit"."""
    if report is None or p.title not in report.placements:
        return ""
    placed = report.placements[p.title]
    if placed is None:
        return f"{STATUS_SKIPPED.split()[0]} would not fit"
    start = f"{placed[0]:%H:%M}"
    if p.time and p.timing in ("preferred", "fixed") and start != p.time:
        return f"{SLOT_ICON} moved to {start}"
    return f"{DONE_ICON} at {start}"


def proposal_rows(proposals: Sequence["ProposedTask"], retrieved: Sequence["KBChunk"],
                  report: "CheckReport | None" = None) -> list[dict]:
    """One numbered row per proposal for a table: what, how long, priority, when, repeats, confidence, how it
    fits the schedule, and the guides it cites."""
    return [
        {
            "#": i,
            "Task": proposal_label(p),
            "Min": p.duration_minutes,
            "Priority": proposal_priority(p),
            "When": proposal_when(p),
            "Repeats": proposal_repeats(p),
            "Confidence": confidence_badge(p.confidence),
            "Fit": proposal_fit(p, report),
            "Sources": proposal_sources(p, retrieved),
        }
        for i, p in enumerate(proposals, 1)
    ]


def no_suggestions_text(retrieved: Sequence["KBChunk"], rejected: Sequence) -> str:
    """Why there is nothing to show: no care guide matched, every proposal failed validation, or nothing is missing."""
    if not retrieved:
        return "No matching care guides were found for this pet, so no suggestion could be backed by one."
    if rejected:
        return "No usable suggestions: every proposal failed validation (the reasons are listed below)."
    return "Nothing new to suggest: the existing tasks already cover the usual care."


def proposal_markdown(p: "ProposedTask") -> str:
    """The headline of a proposal for Streamlit: bold title, a red REQUIRED tag, then the details."""
    tag = " :red[**REQUIRED**]" if p.required else ""
    return (f"{category_icon(Category(p.category))} **{p.title}**{tag}: {p.duration_minutes} min, "
            f"{priority_badge(Priority.from_str(p.priority))}, {proposal_when(p)}, {proposal_repeats(p)}")


def proposal_detail(p: "ProposedTask", retrieved: Sequence["KBChunk"], report: "CheckReport | None" = None) -> str:
    """The small print under a proposal: confidence and why, the reason, the guides it cites, and the schedule fit."""
    fit = proposal_fit(p, report)
    slot = report.suggested_slots.get(p.title) if report else None
    why = f" ({p.confidence_detail})" if p.confidence_detail else ""
    parts = [f"Confidence {confidence_badge(p.confidence)}{why}.", f"{p.rationale}",
             f"Sources: {proposal_sources(p, retrieved)}."]
    if fit:
        parts.append(f"Schedule check: {fit}.")
    if slot:
        parts.append(f"Next free slot: {slot[0]:%H:%M}-{slot[1]:%H:%M}.")
    return " ".join(parts)


def proposal_problems(p: "ProposedTask", report: "CheckReport | None") -> list[str]:
    """The schedule warnings and errors about one proposal, each starting with a clash emoji."""
    if report is None:
        return []
    return [f"{CLASH_ICON} {issue.message}" for issue in report.issues
            if issue.task_title == p.title and issue.severity != "info"]


def general_problems(report: "CheckReport | None") -> list[str]:
    """The schedule warnings and errors that are not about one particular proposal."""
    if report is None:
        return []
    return [f"{CLASH_ICON} {issue.message}" for issue in report.issues
            if issue.task_title is None and issue.severity != "info"]

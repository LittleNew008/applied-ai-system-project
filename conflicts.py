"""Helpers for resolving fixed-time clashes: which task pairs clash, and where a clashing task could move.

`Scheduler.find_conflicts` only returns warning text. The UI also needs to know *which* tasks clash so it
can offer a fix for each, and a free slot that cannot start a new clash. This module reads tasks only
through public `Scheduler` methods (`filter_due_tasks`, `generate_plan`) and never changes anything; the
caller applies a fix with `Pet.edit_task` or `Pet.remove_task`.
"""

from datetime import date, time

from pawpal_system import PetTask, Scheduler, Task, _overlapping_pairs, from_minutes, to_minutes


def conflict_pairs(scheduler: Scheduler, on: date) -> list[tuple[PetTask, PetTask]]:
    """The due fixed-time tasks whose time ranges overlap, as `(earlier, later)` pairs.

    The same pairs, in the same order, as the messages from `Scheduler.find_conflicts(on)`: both
    sort by (start, title) and use the shared `_overlapping_pairs` sweep. Touching ranges do not clash.
    """
    spans: list[tuple[int, int, PetTask]] = []
    for pt in scheduler.filter_due_tasks(on):
        if pt.task.fixed_time is not None:
            start = to_minutes(pt.task.fixed_time)
            spans.append((start, start + pt.task.duration_minutes, pt))
    spans.sort(key=lambda span: (span[0], span[2].task.title))
    return [(a, b) for (_, _, a), (_, _, b) in _overlapping_pairs(spans)]


def find_move_slot(scheduler: Scheduler, task: Task, on: date) -> time | None:
    """A start time where `task` could move without clashing, or None if the day has no room.

    The slot must be free of every other task the plan schedules *and* of every other due fixed-time
    task, including the ones the plan skipped. (`Scheduler.next_available_slot` ignores skipped tasks,
    so it could offer the very time the other half of the clash wants.) It prefers the nearest slot
    that starts at or after the task's current time, and otherwise takes the earliest one before it.
    The daily minutes cap is not considered: moving a task does not change how many minutes it adds.
    """
    owner, minutes = scheduler.owner, task.duration_minutes
    win_start, win_end = to_minutes(owner.day_start), to_minutes(owner.day_end)
    busy = [(to_minutes(i.start), to_minutes(i.end))
            for i in scheduler.generate_plan(on).scheduled_items if i.task.id != task.id]
    for pt in scheduler.filter_due_tasks(on):
        if pt.task.fixed_time is not None and pt.task.id != task.id:
            start = to_minutes(pt.task.fixed_time)
            busy.append((start, start + pt.task.duration_minutes))

    gaps, cursor = [], win_start  # the free intervals of the day, in time order
    for start, end in sorted(busy):
        if start > cursor:
            gaps.append((cursor, min(start, win_end)))
        cursor = max(cursor, end)
    if cursor < win_end:
        gaps.append((cursor, win_end))

    own = to_minutes(task.fixed_time) if task.fixed_time is not None else win_start
    for gap_start, gap_end in gaps:
        start = max(gap_start, own)
        if gap_end - start >= minutes and start != own:
            return from_minutes(start)
    for gap_start, gap_end in gaps:
        if gap_end - gap_start >= minutes and gap_start < own:
            return from_minutes(gap_start)
    return None

"""Demo: build an owner with two pets, then show sorting, filtering, conflict warnings, the next free
slot, today's schedule, recurring tasks and saving to data.json in the terminal.

Run from the project root:  python main.py            (the demo)
                            python main.py assist     (the Care Assistant: suggest tasks, pick, add)
"""

import argparse
import tempfile
from collections.abc import Callable
from datetime import date, time
from pathlib import Path

import ai_assistant
import display
from pawpal_system import Category, DailyPlan, Owner, Pet, PetTask, Priority, Recurrence, Scheduler, Task

RULE = "=" * 72


def build_owner() -> Owner:
    owner = Owner("Jordan", day_start=time(7, 0), day_end=time(20, 0))

    biscuit = Pet("Biscuit", "dog", breed="Golden Retriever", age=4)
    mochi = Pet("Mochi", "cat", breed="Siamese", age=2)

    # Tasks are added deliberately OUT of time order, so the sorting below has work to do.
    biscuit.add_task(Task(
        "Evening fetch", 25, priority=Priority.MEDIUM, category=Category.ENRICHMENT,
        preferred_start=time(18, 0),
    ))
    biscuit.add_task(Task(
        "Lunch feeding", 10, priority=Priority.HIGH, category=Category.FEEDING,
        fixed_time=time(12, 0), recurrence=Recurrence.DAILY,
    ))
    mochi.add_task(Task(
        "Laser-pointer play", 20, priority=Priority.MEDIUM, category=Category.ENRICHMENT,
        preferred_start=time(15, 30),
    ))
    biscuit.add_task(Task(
        "Heart medication", 5, priority=Priority.HIGH, category=Category.MEDS, required=True,
        fixed_time=time(8, 0), recurrence=Recurrence.DAILY,
        description="One pill, hidden in a bit of cheese",
    ))
    mochi.add_task(Task("Flea treatment", 5, priority=Priority.LOW))  # no time at all
    mochi.add_task(Task(
        "Vet check-in call", 15, priority=Priority.MEDIUM,
        fixed_time=time(12, 0),  # same time as Biscuit's lunch: a deliberate clash
    ))
    biscuit.add_task(Task(
        "Morning walk", 30, priority=Priority.HIGH, category=Category.WALK,
        preferred_start=time(7, 30), recurrence=Recurrence.DAILY,
    ))
    mochi.add_task(Task(
        "Brush fur", 15, priority=Priority.LOW, category=Category.GROOMING,
        preferred_start=time(9, 0), recurrence=Recurrence.WEEKLY,
    ))

    owner.add_pet(biscuit)
    owner.add_pet(mochi)
    return owner


def show_tasks(title: str, pairs: list[PetTask], today: date) -> None:
    print(title)
    print(display.render_table(display.task_rows(pairs, today)))
    print()


def print_schedule(owner: Owner, plan: DailyPlan) -> None:
    print(f"{display.PET_HEADING} Today's Schedule - {plan.on:%A, %B %d, %Y}")
    print(f"Owner: {owner.name} | Pets: {', '.join(display.pet_label(p) for p in owner.pets)}")
    print(f"Available: {owner.day_start:%H:%M}-{owner.day_end:%H:%M}")
    print(RULE)
    print(display.render_table(display.schedule_rows(plan)))
    if plan.skipped_tasks:
        print("\nNot scheduled:")
        print(display.render_table(display.not_scheduled_rows(plan)))
    for skipped in plan.skipped_tasks:
        icon = display.STATUS_FLAGGED.split()[0] if skipped.flagged else display.CLASH_ICON
        print(f"{icon} {skipped.task.title} ({skipped.pet.name}): {skipped.reason}")
    print(RULE)
    print(plan.summary())


def main() -> None:
    owner = build_owner()
    scheduler = Scheduler(owner)
    today = date.today()

    print(RULE)
    show_tasks("1. Tasks in the order they were added (out of time order):", owner.get_all_tasks(), today)

    show_tasks("2. Sorted by time - Scheduler.sort_by_time():", scheduler.sort_by_time(owner.get_all_tasks()), today)

    show_tasks("3. Filtered by pet - Scheduler.filter_tasks(pet_name='mochi'):",
               scheduler.filter_tasks(pet_name="mochi"), today)

    print("4. Conflict warnings - Scheduler.find_conflicts():")
    for message in scheduler.find_conflicts(today) or ["No conflicts."]:
        print(f"  {display.CLASH_ICON} {message}")
    print()

    print("5. Next free slot - Scheduler.next_available_slot():")
    vet = next(pt.task for pt in owner.get_all_tasks() if pt.task.title == "Vet check-in call")
    plan = scheduler.generate_plan(today)
    for label, minutes, after in (
        ("A new 30-minute task", 30, None),
        (f"'{vet.title}' ({vet.duration_minutes} min), after its 12:00 clash", vet.duration_minutes, vet.fixed_time),
    ):
        slot = scheduler.next_available_slot(today, minutes, not_before=after)
        found = f"{slot[0]:%H:%M}-{slot[1]:%H:%M}" if slot else "none left today"
        print(f"  {display.SLOT_ICON} {label} can go at: {found}")
    print()

    print(RULE)
    print_schedule(owner, plan)
    print()

    print(RULE)
    print("6. Recurring tasks - Scheduler.mark_task_complete():")
    for pt in scheduler.sort_by_time(owner.get_all_tasks()):
        if pt.task.title in ("Heart medication", "Brush fur", "Flea treatment"):
            next_task = scheduler.mark_task_complete(pt.task.id, today)
            if next_task is None:
                print(f"  {display.DONE_ICON} {pt.task.title} ({pt.pet.name}) is {pt.task.recurrence.value}: done, nothing new created.")
            else:
                print(f"  {display.REPEAT_ICON} {pt.task.title} ({pt.pet.name}) is {pt.task.recurrence.value}: done today, "
                      f"new one due {next_task.start_date:%A, %B %d}.")
    print()
    show_tasks("7. Filtered by status - completed=True:", scheduler.filter_tasks(completed=True), today)
    show_tasks("   Filtered by status - completed=False (open):",
               scheduler.sort_by_time(scheduler.filter_tasks(completed=False)), today)

    print("8. Saving and loading - Owner.save_to_json() / Owner.load_from_json():")
    with tempfile.TemporaryDirectory() as folder:  # a temporary folder, so the demo never touches a real data.json
        path = Path(folder) / "data.json"
        owner.save_to_json(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        print(f"  {display.SAVED_ICON} Saved {len(owner.pets)} pets and {len(owner.get_all_tasks())} tasks to data.json "
              f"({path.stat().st_size} bytes, {len(lines)} lines). The start of the file:")
        for line in [l for l in lines if '"id"' not in l][:12]:  # skip the random ids so the output repeats
            print(f"      {line}")
        print("      ...")
        loaded = Owner.load_from_json(path)
        print(f"  {display.SAVED_ICON} Loaded it back: {len(loaded.pets)} pets and {len(loaded.get_all_tasks())} tasks; "
              f"identical to the original: {loaded == owner}")


# ---------------------------------------------------------------- Care Assistant (command line)

def default_picks(result: ai_assistant.AssistantResult) -> list[int]:
    """The suggestions (1-based) that start ticked: not required, and confident enough. Same rule as the app."""
    return [i for i, p in enumerate(result.accepted, 1)
            if not p.required and p.confidence >= ai_assistant.LOW_CONFIDENCE]


def parse_selection(text: str, count: int, default: list[int]) -> list[int]:
    """Turn what the user typed into 1-based suggestion numbers.

    Blank means `default`; "all" and "none" mean what they say; otherwise numbers separated by commas or spaces
    ("1,3"), each from 1 to `count`. Raises ValueError with a readable message for anything else.
    """
    words = text.replace(",", " ").split()
    if not words:
        return list(default)
    if len(words) == 1 and words[0].lower() in ("all", "a"):
        return list(range(1, count + 1))
    if len(words) == 1 and words[0].lower() in ("none", "n", "0", "q", "quit"):
        return []
    picks = []
    for word in words:
        if not word.isdigit() or not 1 <= int(word) <= count:
            raise ValueError(f"{word!r} is not a suggestion number from 1 to {count} (or 'all' / 'none')")
        if int(word) not in picks:
            picks.append(int(word))
    return sorted(picks)


def show_suggestions(pet: Pet, on: date, result: ai_assistant.AssistantResult, config: ai_assistant.AssistantConfig) -> None:
    """Print what the assistant found: mode, needs, guides used, the table of suggestions, and any problems."""
    print(f"{display.ASSISTANT_ICON} Care Assistant for {display.pet_label(pet)} - {on:%A, %B %d, %Y}")
    if config.offline:
        print(f"Mode: offline ({config.offline_reason}); suggestions come from the built-in care guides.")
    else:
        print(f"Mode: Gemini ({config.model}); the pet's details and task titles were sent to Google.")
    if result.fell_back:
        print(f"{display.CLASH_ICON} Gemini could not be used ({result.fallback_reason}); "
              "these suggestions come from the built-in care guides.")
    print(RULE)
    print("Needs:")
    for need in result.needs:
        print(f"  - {need}")
    if result.retrieved:
        print("Care guides used:")
        for chunk in result.retrieved:
            print(f"  - {chunk.title} [{chunk.id}]")
    print()
    if not result.accepted:
        print("Nothing new to suggest: the existing tasks already cover the usual care.")
    else:
        print(display.render_table(display.proposal_rows(result.accepted, result.retrieved, result.report),
                                   wrap_column="Sources", wrap_width=34))
        for number, p in enumerate(result.accepted, 1):
            for problem in display.proposal_problems(p, result.report):
                print(f"  {number}. {problem}")
        for problem in display.general_problems(result.report):
            print(f"  {problem}")
        print(f"(Confidence: {ai_assistant.CONFIDENCE_WEIGHTS['model']:.0%} the model's own figure, "
              f"{ai_assistant.CONFIDENCE_WEIGHTS['grounding']:.0%} guide support, "
              f"{ai_assistant.CONFIDENCE_WEIGHTS['fit']:.0%} schedule fit. Required tasks are marked ⭐.)")
    if result.rejected:
        print("Left out:")
        for rejection in result.rejected:
            print(f"  - {rejection.reason}")
    print(RULE)


def choose_suggestions(result: ai_assistant.AssistantResult, accept: str | None,
                       input_fn: Callable[[str], str]) -> list[int]:
    """Which suggestions to add: `accept` if given (raises ValueError if it is invalid), else ask the user,
    asking again up to three times after a mistake. End of input counts as "none"."""
    default = default_picks(result)
    count = len(result.accepted)
    if accept is not None:
        return parse_selection(accept, count, default)
    prompt = (f"Add which? numbers like 1,3 | all | none | Enter = {', '.join(map(str, default)) or 'none'}: ")
    for _ in range(3):
        try:
            return parse_selection(input_fn(prompt), count, default)
        except ValueError as err:
            print(f"  {err}")
        except EOFError:
            return []
    print("  No valid choice, so nothing was added.")
    return []


def assistant_flow(owner: Owner, pet: Pet, on: date, *, accept: str | None = None,
                   input_fn: Callable[[str], str] = input,
                   config: ai_assistant.AssistantConfig | None = None) -> list[Task]:
    """Suggest tasks for `pet`, let the user choose, add the chosen ones, and show the new schedule.

    The same steps as the app's Care Assistant panel. `accept` ("all", "none", "1,3", or "" for the default)
    skips the question. Chosen suggestions are added with `ai_assistant.commit`, which uses `Pet.add_task`.
    Returns the tasks added.
    """
    config = config or ai_assistant.AssistantConfig.from_env()
    try:
        result = ai_assistant.propose_care_plan(owner, pet, on, config=config)
    except ai_assistant.InvalidPetData as err:
        print(f"{display.ASSISTANT_ICON} Care Assistant was not asked: {pet.name}'s details need fixing:")
        for problem in err.problems:
            print(f"  {display.CLASH_ICON} {problem}")
        return []
    show_suggestions(pet, on, result, config)
    if not result.accepted:
        return []

    picks = choose_suggestions(result, accept, input_fn)
    if not picks:
        print("Nothing added.")
        return []
    created = ai_assistant.commit(owner, pet, result, [result.accepted[i - 1].title for i in picks], on)
    print(f"{display.DONE_ICON} Added {len(created)} task(s) to {pet.name}: " + ", ".join(t.title for t in created))
    print()
    print_schedule(owner, Scheduler(owner).generate_plan(on))
    print(f"(Full step log of this run: {config.trace_path.name}, run id {result.trace_id}.)")
    return created


def run_assist(args: argparse.Namespace) -> int:
    """`python main.py assist`: load the owner, pick the pet, run the flow, and save if --data was given."""
    if args.data:
        try:
            owner = Owner.load_from_json(args.data)
        except FileNotFoundError:
            print(f"No such data file: {args.data}")
            return 1
        except ValueError as err:
            print(f"Could not read {args.data}: {err}")
            return 1
    else:
        owner = build_owner()  # the demo owner; nothing is saved
    if not owner.pets:
        print("This owner has no pets yet; add one first.")
        return 1
    if args.pet:
        pet = next((p for p in owner.pets if p.name.casefold() == args.pet.casefold()), None)
        if pet is None:
            print(f"No pet named {args.pet!r}. Pets: {', '.join(p.name for p in owner.pets)}")
            return 1
    else:
        pet = owner.pets[0]
    try:
        created = assistant_flow(owner, pet, args.date, accept=args.accept)
    except ValueError as err:  # a bad --accept value
        print(f"Invalid --accept: {err}")
        return 2
    if created and args.data:
        owner.save_to_json(args.data)
        print(f"{display.SAVED_ICON} Saved to {args.data}.")
    return 0


def cli(argv: list[str] | None = None) -> int:
    """Command line entry point: no command runs the demo; `assist` runs the Care Assistant."""
    parser = argparse.ArgumentParser(prog="main.py", description="PawPal+ command-line demo and Care Assistant.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("demo", help="the scheduler demo (the default)")
    assist = commands.add_parser("assist", help="get task suggestions for a pet, choose, and add them")
    assist.add_argument("--pet", help="the pet's name (default: the first pet)")
    assist.add_argument("--data", help="a data.json to load, and to save back to if you add tasks "
                                       "(default: the demo owner, nothing saved)")
    assist.add_argument("--accept", help='skip the question: "all", "none", numbers like "1,3", or "" for the default')
    assist.add_argument("--date", type=date.fromisoformat, default=date.today(), metavar="YYYY-MM-DD",
                        help="the day to plan for (default: today)")
    args = parser.parse_args(argv)
    if args.command == "assist":
        ai_assistant.load_env_file()  # the Gemini key and other settings from the git-ignored .env, if there is one
        return run_assist(args)
    main()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())

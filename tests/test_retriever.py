"""Unit tests of the knowledge-base retriever (ai_assistant.retrieve / search_knowledge). No network."""

from pathlib import Path

import pytest

import ai_assistant as ai
from pawpal_system import Pet

KB_DIR = Path(__file__).resolve().parent.parent / "knowledge"


@pytest.fixture(scope="module")
def kb():
    return ai.load_knowledge_base(KB_DIR)


def ids(hits):
    return [chunk.id for chunk, _ in hits]


def chunk(id_, tags, text="", title=None):
    return ai.KBChunk(id_, title or id_, text, frozenset(tags))


# ------------------------------------------------------------------ the shipped knowledge base

def test_the_knowledge_base_covers_dog_cat_feeding_exercise_grooming_and_vet_basics(kb):
    tags = set().union(*(c.tags for c in kb))
    assert {"dog", "cat", "feeding", "exercise", "grooming", "vet"} <= tags
    assert len({c.id for c in kb}) == len(kb)


# ------------------------------------------------------------------ retrieve (terms in, chunks out)

def test_retrieve_scores_tag_matches_above_text_matches():
    kb = [chunk("tagged", {"walk"}), chunk("texty", set(), text="a long walk helps")]
    assert [(c.id, s) for c, s in ai.retrieve(["walk"], kb)] == [("tagged", 2.0), ("texty", 0.5)]


def test_retrieve_returns_at_most_k_best_first_and_drops_zero_scores():
    kb = [chunk("a", {"x"}), chunk("b", {"x", "y"}), chunk("c", {"y"}), chunk("d", {"z"})]
    hits = ai.retrieve(["x", "y"], kb, k=2)
    assert ids(hits) == ["b", "a"]  # b matches both terms; a and c tie, and the id breaks the tie
    assert [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)
    assert ai.retrieve(["x", "y"], kb, k=10) == [(kb[1], 4.0), (kb[0], 2.0), (kb[2], 2.0)]  # d (score 0) is dropped


def test_retrieve_breaks_ties_by_chunk_id_whatever_the_input_order():
    kb = [chunk("b", {"x"}), chunk("a", {"x"}), chunk("c", {"x"})]
    assert ids(ai.retrieve(["x"], kb)) == ["a", "b", "c"]
    assert ids(ai.retrieve(["x"], kb[::-1])) == ["a", "b", "c"]


def test_retrieve_is_case_insensitive_and_ignores_blank_terms():
    kb = [chunk("a", {"walk"})]
    assert ids(ai.retrieve(["  WALK ", "", "   "], kb)) == ["a"]
    assert ai.retrieve([], kb) == [] and ai.retrieve(["", " "], kb) == []


def test_retrieve_with_an_empty_knowledge_base_or_no_match_returns_nothing():
    assert ai.retrieve(["walk"], []) == []
    assert ai.retrieve(["zzz"], [chunk("a", {"walk"})]) == []


def test_retrieve_never_returns_another_species_guide():
    kb = [chunk("dog-feed", {"dog", "feeding"}), chunk("cat-feed", {"cat", "feeding"}), chunk("any", {"feeding"})]
    assert sorted(ids(ai.retrieve(["feeding"], kb, species="dog"))) == ["any", "dog-feed"]
    assert ids(ai.retrieve(["feeding"], kb, species="OTHER")) == ["any"]  # a species with no guide gets general ones
    assert set(ids(ai.retrieve(["feeding"], kb))) == {"dog-feed", "cat-feed", "any"}  # no species given: no filter


def test_retrieve_returns_a_life_stage_guide_only_when_that_stage_is_asked_for():
    kb = [chunk("senior-care", {"senior", "walk"}), chunk("walks", {"walk"})]
    assert ids(ai.retrieve(["walk"], kb)) == ["walks"]
    assert ids(ai.retrieve(["walk", "senior"], kb)) == ["senior-care", "walks"]
    assert ids(ai.retrieve(["walk", "young"], kb)) == ["walks"]


# ------------------------------------------------------------------ search_terms

def test_search_terms_drop_stopwords_strip_plurals_and_map_everyday_words():
    pet = Pet("Rex", "dog", age=4)
    terms = ai.search_terms(pet, "How often should I brush my dog's teeth?")
    assert {"brush", "grooming", "teeth", "dog"} <= set(terms)
    assert not {"how", "should", "i", "my"} & set(terms)
    walks = ai.search_terms(pet, "walks")
    assert "walk" in walks and "walks" in walks


def test_search_terms_add_the_pets_species_life_stage_and_special_needs():
    assert ai.search_terms(Pet("A", "Cat", age=0)) == ["cat", "young"]
    assert ai.search_terms(Pet("B", "dog", age=9)) == ["dog", "senior"]
    assert ai.search_terms(Pet("C", "dog", age=4)) == ["dog"]
    terms = ai.search_terms(Pet("D", "dog", age=4, special_needs="a daily pill for arthritis"))
    assert {"daily", "pill", "meds", "medication", "arthritis"} <= set(terms)
    assert len(terms) == len(set(terms))  # no duplicates


# ------------------------------------------------------------------ search_knowledge (pet + query in, chunks out)

@pytest.mark.parametrize("species, query, expected_first", [
    ("dog", "how often should I brush my dog's teeth", "dog-grooming"),
    ("dog", "how much exercise does my dog need", "dog-exercise"),
    ("dog", "what should I feed and when", "dog-feeding"),
    ("cat", "my cat is bored, what toys help", "cat-play"),
    ("cat", "when should I scoop the litter", "cat-grooming"),
    ("cat", "how often should I feed my cat", "cat-feeding"),
    ("dog", "vaccination and checkup schedule", "vet-basics"),
    ("cat", "when should I call the doctor, she seems sick", "vet-basics"),
])
def test_search_knowledge_finds_the_right_guide_first(kb, species, query, expected_first):
    hits = ai.search_knowledge(Pet("P", species, age=4), query, kb, k=3)
    assert hits and hits[0][0].id == expected_first


def test_search_knowledge_never_mixes_species_even_for_a_cross_species_question(kb):
    dog_hits = ai.search_knowledge(Pet("Rex", "dog", age=4), "litter box and cat toys", kb, k=10)
    assert not any(c.id.startswith("cat-") for c, _ in dog_hits)
    cat_hits = ai.search_knowledge(Pet("Tom", "cat", age=4), "walk the dog", kb, k=10)
    assert not any(c.id.startswith("dog-") for c, _ in cat_hits)


def test_search_knowledge_respects_k_and_orders_by_score(kb):
    pet = Pet("Rex", "dog", age=4)
    everything = ai.search_knowledge(pet, "walk feeding grooming", kb, k=50)
    for k in (1, 2, 5):
        hits = ai.search_knowledge(pet, "walk feeding grooming", kb, k=k)
        assert hits == everything[:k]
        assert [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)
    assert ai.search_knowledge(pet, "walk", kb, k=0) == []


def test_search_knowledge_is_deterministic(kb):
    pet = Pet("Rex", "dog", age=9, special_needs="daily medication")
    first = ai.search_knowledge(pet, "exercise and pills", kb, k=4)
    assert first == ai.search_knowledge(pet, "exercise and pills", kb, k=4)
    assert first == ai.search_knowledge(pet, "exercise and pills", list(reversed(kb)), k=4)


def test_a_special_need_alone_pulls_in_the_medication_guide(kb):
    pet = Pet("Rex", "dog", age=4, special_needs="needs a daily pill")
    assert "medication-general" in ids(ai.search_knowledge(pet, "", kb, k=4))
    assert "medication-general" not in ids(ai.search_knowledge(Pet("Rex", "dog", age=4), "", kb, k=10))


def test_life_stage_guides_follow_the_pets_age(kb):
    assert "senior-care" in ids(ai.search_knowledge(Pet("Old", "dog", age=11), "walk", kb, k=10))
    assert "senior-care" not in ids(ai.search_knowledge(Pet("Mid", "dog", age=4), "walk", kb, k=10))
    assert "young-pet-care" in ids(ai.search_knowledge(Pet("Pup", "dog", age=0), "training", kb, k=10))
    assert "young-pet-care" not in ids(ai.search_knowledge(Pet("Mid", "dog", age=4), "training", kb, k=10))


def test_a_query_with_only_stopwords_or_nonsense_falls_back_to_the_pets_own_species(kb):
    pet = Pet("Rex", "dog", age=4)
    assert ai.search_knowledge(pet, "the and of", kb, k=3) == ai.search_knowledge(pet, "", kb, k=3)
    assert all("dog" in c.tags for c, _ in ai.search_knowledge(pet, "qwertyuiop", kb, k=3))


def test_an_unknown_species_with_nonsense_gets_nothing_but_with_a_topic_gets_general_guides(kb):
    pet = Pet("Polly", "parrot", age=4)
    assert ai.search_knowledge(pet, "qwertyuiop", kb) == []
    assert "vet-basics" in ids(ai.search_knowledge(pet, "vaccination", kb))


def test_search_knowledge_on_an_empty_knowledge_base_returns_nothing():
    assert ai.search_knowledge(Pet("Rex", "dog"), "walk", []) == []

"""
Supersession and exclusion behaviour of `TaxonomyRobot._loop_task`.

Covers the four cases documented in `_loop_task`, one test class each:
  1) excluded + no existing taxonomy      -> do nothing
  2) excluded + existing taxonomy         -> tombstone superseding the taxonomy enhancement
  3) relevant + no existing taxonomy      -> classify and write a new LDE
  4) relevant + existing taxonomy         -> no-op, or classify and write an LDE that
                                             supersedes the existing one (`supersede_existing`)

Plus supersession-chain states in the fake batch data (chains, tombstoned chains,
duplicates) so we pin down which enhancement the robot picks as supersession target.
"""

import asyncio
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from destiny_sdk.enhancements import (
    AnnotationEnhancement,
    BooleanAnnotation,
    Enhancement,
    LinkedDataEnhancement,
    TombstoneEnhancement,
)
from destiny_sdk.references import Reference
from destiny_sdk.robots import EnhancementResultEntry, LinkedRobotError, RobotEnhancementBatch
from destiny_sdk.visibility import Visibility

from app.robots.robot_taxonomy import TaxonomyRobot

if TYPE_CHECKING:
    from pydantic import HttpUrl

    from app.util.repository import Repository

# Pinned on each test robot's settings copy, so fake data and robot agree regardless of defaults.
UPSTREAM_SCHEME = "domain-inclusion"
UPSTREAM_LABEL = "destiny-high-precision"

VOCAB_URI = "https://vocab.example.org/v1"
CONTEXT_URI = "https://vocab.example.org/context.jsonld"


# --------------------------------------------------------------------------- fake data builders


def _make_reference(enhancements: Sequence[Enhancement] | None = None) -> Reference:
    return Reference(id=uuid4(), enhancements=list(enhancements) if enhancements is not None else None)


def _make_lde(reference_id: UUID, *, supersedes: UUID | None = None, source: str = TaxonomyRobot.NAME) -> Enhancement:
    return Enhancement(
        id=uuid4(),
        reference_id=reference_id,
        source=source,
        visibility=Visibility.PUBLIC,
        supersedes=supersedes,
        content=LinkedDataEnhancement(vocabulary_uri=cast("HttpUrl", VOCAB_URI), data={"@context": CONTEXT_URI, "seed": "existing"}),
    )


def _make_inclusion(
    reference_id: UUID,
    *,
    value: bool,
    supersedes: UUID | None = None,
    scheme: str = UPSTREAM_SCHEME,
    label: str = UPSTREAM_LABEL,
) -> Enhancement:
    return Enhancement(
        id=uuid4(),
        reference_id=reference_id,
        source="upstream-inclusion-robot",
        visibility=Visibility.PUBLIC,
        supersedes=supersedes,
        content=AnnotationEnhancement(annotations=[BooleanAnnotation(scheme=scheme, label=label, value=value)]),
    )


def _make_tombstone(reference_id: UUID, *, supersedes: UUID | None) -> Enhancement:
    return Enhancement(
        id=uuid4(),
        reference_id=reference_id,
        source=TaxonomyRobot.NAME,
        visibility=Visibility.PUBLIC,
        supersedes=supersedes,
        content=TombstoneEnhancement(reason="withdrawn in test data"),
    )


# --------------------------------------------------------------------------- test doubles


class _FakeRepository:
    """Stands in for `Repository`: serves one prepared batch and captures submissions."""

    def __init__(self, references: list[Reference], *, empty_batch_info: bool = False) -> None:
        self.batch = RobotEnhancementBatch(
            id=uuid4(),
            reference_storage_url=cast("HttpUrl", "https://example.test/refs.jsonl"),
            result_storage_url=cast("HttpUrl", "https://example.test/results.jsonl"),
        )
        self.references = references
        # Simulates the repository handing back a batch lease but no references at all.
        self._empty_batch_info = empty_batch_info
        self.submitted: list[EnhancementResultEntry] | None = None
        self.submit_calls = 0

    async def get_next_batch(self, batch_size: int | None = None) -> tuple[RobotEnhancementBatch | None, list[Reference] | None]:
        if self._empty_batch_info:
            return None, None
        return self.batch, self.references

    async def submit_enhancements(self, batch_info: RobotEnhancementBatch, enhancements: Sequence[EnhancementResultEntry]) -> None:
        assert batch_info is self.batch
        self.submit_calls += 1
        self.submitted = list(enhancements)


class _FakeClassifier:
    """Stands in for `TaxonomyRobot._annotate_reference`: records calls, returns canned concepts."""

    def __init__(self, concepts: dict[UUID, list[str]] | None = None) -> None:
        self.concepts = concepts or {}
        self.classified: list[UUID] = []

    async def __call__(self, reference: Reference) -> list[str]:
        self.classified.append(reference.id)
        return self.concepts.get(reference.id, [f"{VOCAB_URI}/concept/{reference.id}"])


# --------------------------------------------------------------------------- harness


RobotFactory = Callable[..., tuple[TaxonomyRobot, _FakeRepository, _FakeClassifier]]


@pytest.fixture
def make_robot(monkeypatch: pytest.MonkeyPatch) -> RobotFactory:
    def make(
        references: list[Reference],
        *,
        supersede_existing: bool = False,
        empty_batch_info: bool = False,
    ) -> tuple[TaxonomyRobot, _FakeRepository, _FakeClassifier]:
        # Keep the real LLM extractor out of the loop; the fake classifier replaces _annotate_reference below.
        monkeypatch.setattr("app.robots.robot_taxonomy.TaxonomyExtractor", lambda _settings: SimpleNamespace())

        robot = TaxonomyRobot(name="taxonomy")
        # get_settings() is cached and shared, so mutate a copy instead of the global instance.
        robot.settings = robot.settings.model_copy(
            update={
                "supersede_existing": supersede_existing,
                "upstream_scheme": UPSTREAM_SCHEME,
                "upstream_label": UPSTREAM_LABEL,
            },
        )

        repository = _FakeRepository(references, empty_batch_info=empty_batch_info)
        robot.repository = cast("Repository", repository)

        classifier = _FakeClassifier()
        setattr(robot, "_annotate_reference", classifier)  # noqa: B010

        return robot, repository, classifier

    return make


def _submitted_enhancements(repository: _FakeRepository) -> list[Enhancement]:
    assert repository.submitted is not None, "robot never submitted"
    return [entry for entry in repository.submitted if isinstance(entry, Enhancement)]


def _submitted_errors(repository: _FakeRepository) -> list[LinkedRobotError]:
    assert repository.submitted is not None, "robot never submitted"
    return [entry for entry in repository.submitted if isinstance(entry, LinkedRobotError)]


def _concepts_of(enhancement: Enhancement) -> list[str]:
    assert isinstance(enhancement.content, LinkedDataEnhancement)
    investigation = enhancement.content.data["hasInvestigation"]
    assert isinstance(investigation, dict)
    applied = investigation["hasAppliedConcept"]
    assert isinstance(applied, list)
    return [str(uri) for uri in applied]


def _run_loop_assert_batch_processed(robot: TaxonomyRobot) -> None:
    """Run one loop iteration and assert a batch was processed."""
    assert asyncio.run(robot._loop_task()) is True  # noqa: SLF001


# --------------------------------------------------------------------------- case 1


class TestCase1ExcludedWithoutExistingTaxonomy:
    """Case 1: reference is excluded and has no taxonomy enhancement -> do nothing."""

    def test_does_nothing(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        reference.enhancements = [_make_inclusion(reference.id, value=False)]

        robot, repository, classifier = make_robot([reference])

        _run_loop_assert_batch_processed(robot)
        assert _submitted_enhancements(repository) == []
        assert _submitted_errors(repository) == []
        assert classifier.classified == []

    @pytest.mark.parametrize(
        ("value", "scheme", "label"),
        [
            pytest.param(True, UPSTREAM_SCHEME, UPSTREAM_LABEL, id="value-true"),
            pytest.param(False, "other-scheme", UPSTREAM_LABEL, id="other-scheme"),
            pytest.param(False, UPSTREAM_SCHEME, "other-label", id="other-label"),
        ],
    )
    def test_non_matching_inclusion_annotation_is_not_an_exclusion(
        self,
        make_robot: RobotFactory,
        value: bool,
        scheme: str,
        label: str,
    ) -> None:
        reference = _make_reference()
        reference.enhancements = [_make_inclusion(reference.id, value=value, scheme=scheme, label=label)]

        robot, repository, classifier = make_robot([reference])

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == [reference.id]
        (lde,) = _submitted_enhancements(repository)
        assert lde.supersedes is None  # not excluded, no existing taxonomy -> plain new LDE (case 3)


# --------------------------------------------------------------------------- case 2


class TestCase2ExcludedWithExistingTaxonomy:
    """Case 2: reference is excluded but has a live taxonomy enhancement -> tombstone it."""

    def test_writes_superseding_tombstone(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        taxonomy_enh = _make_lde(reference.id)
        inclusion_enh = _make_inclusion(reference.id, value=False)
        reference.enhancements = [inclusion_enh, taxonomy_enh]

        robot, repository, classifier = make_robot([reference])  # supersede_existing=False: retirement must happen anyway

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == []
        assert _submitted_errors(repository) == []
        (tombstone,) = _submitted_enhancements(repository)
        assert isinstance(tombstone.content, TombstoneEnhancement)
        assert tombstone.supersedes == taxonomy_enh.id
        assert tombstone.reference_id == reference.id
        assert tombstone.source == TaxonomyRobot.NAME
        assert tombstone.visibility == Visibility.PUBLIC
        assert inclusion_enh.id is not None
        assert str(inclusion_enh.id) in (tombstone.content.reason or "")

    def test_tombstone_written_even_with_supersede_existing_enabled(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        taxonomy_enh = _make_lde(reference.id)
        reference.enhancements = [_make_inclusion(reference.id, value=False), taxonomy_enh]

        robot, repository, classifier = make_robot([reference], supersede_existing=True)

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == []
        (tombstone,) = _submitted_enhancements(repository)
        assert isinstance(tombstone.content, TombstoneEnhancement)
        assert tombstone.supersedes == taxonomy_enh.id

    def test_already_tombstoned_taxonomy_is_not_retired_again(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        taxonomy_enh = _make_lde(reference.id)
        reference.enhancements = [
            _make_inclusion(reference.id, value=False),
            taxonomy_enh,
            _make_tombstone(reference.id, supersedes=taxonomy_enh.id),
        ]

        robot, repository, classifier = make_robot([reference])

        _run_loop_assert_batch_processed(robot)
        assert _submitted_enhancements(repository) == []
        assert classifier.classified == []


# --------------------------------------------------------------------------- case 3


class TestCase3RelevantWithoutExistingTaxonomy:
    """Case 3: reference is relevant and has no live taxonomy enhancement -> classify and write a new LDE."""

    def test_writes_new_lde_without_supersession(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()

        robot, repository, classifier = make_robot([reference])

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == [reference.id]
        (lde,) = _submitted_enhancements(repository)
        assert isinstance(lde.content, LinkedDataEnhancement)
        assert lde.supersedes is None
        assert lde.reference_id == reference.id
        assert lde.source == TaxonomyRobot.NAME
        assert _concepts_of(lde) == [f"{VOCAB_URI}/concept/{reference.id}"]

    def test_foreign_lde_does_not_count_as_existing_taxonomy(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        foreign_lde = _make_lde(reference.id, source="some-other-robot")
        reference.enhancements = [foreign_lde]

        robot, repository, classifier = make_robot([reference], supersede_existing=True)

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == [reference.id]
        (lde,) = _submitted_enhancements(repository)
        assert lde.supersedes is None  # new chain, foreign enhancement untouched

    def test_lde_tombstoned_before_restart_starts_a_new_chain(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        taxonomy_enh = _make_lde(reference.id)
        reference.enhancements = [taxonomy_enh, _make_tombstone(reference.id, supersedes=taxonomy_enh.id)]

        robot, repository, classifier = make_robot([reference], supersede_existing=True)

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == [reference.id]
        (lde,) = _submitted_enhancements(repository)
        assert isinstance(lde.content, LinkedDataEnhancement)
        assert lde.supersedes is None  # the withdrawn chain must not be superseded again

    def test_superseded_exclusion_annotation_no_longer_excludes(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        old_exclusion = _make_inclusion(reference.id, value=False)
        reference.enhancements = [old_exclusion, _make_inclusion(reference.id, value=True, supersedes=old_exclusion.id)]

        robot, repository, classifier = make_robot([reference])

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == [reference.id]
        (lde,) = _submitted_enhancements(repository)
        assert lde.supersedes is None


# --------------------------------------------------------------------------- case 4


class TestCase4RelevantWithExistingTaxonomy:
    """Case 4: reference is relevant and has a live taxonomy enhancement -> no-op, or supersede when enabled."""

    def test_is_a_noop_when_supersede_existing_false(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        reference.enhancements = [_make_inclusion(reference.id, value=True), _make_lde(reference.id)]

        robot, repository, classifier = make_robot([reference], supersede_existing=False)

        _run_loop_assert_batch_processed(robot)
        assert _submitted_enhancements(repository) == []
        assert _submitted_errors(repository) == []
        assert classifier.classified == []

    def test_supersedes_existing_lde_when_enabled(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        taxonomy_enh = _make_lde(reference.id)
        reference.enhancements = [_make_inclusion(reference.id, value=True), taxonomy_enh]

        robot, repository, classifier = make_robot([reference], supersede_existing=True)

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == [reference.id]
        (lde,) = _submitted_enhancements(repository)
        assert isinstance(lde.content, LinkedDataEnhancement)
        assert lde.supersedes == taxonomy_enh.id
        assert _concepts_of(lde) == [f"{VOCAB_URI}/concept/{reference.id}"]

    def test_supersedes_the_chain_head_not_the_first_enhancement(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        first = _make_lde(reference.id)
        head = _make_lde(reference.id, supersedes=first.id)
        reference.enhancements = [first, head]

        robot, repository, _classifier = make_robot([reference], supersede_existing=True)

        _run_loop_assert_batch_processed(robot)
        (lde,) = _submitted_enhancements(repository)
        assert lde.supersedes == head.id  # the superseded `first` must not be targeted twice

    def test_tombstone_over_one_duplicate_lde_supersedes_the_survivor(self, make_robot: RobotFactory) -> None:
        """Inconsistent ODS state: two live LDEs, one of them retired -> supersession targets the other."""
        reference = _make_reference()
        retired, survivor = _make_lde(reference.id), _make_lde(reference.id)
        reference.enhancements = [retired, survivor, _make_tombstone(reference.id, supersedes=retired.id)]

        robot, repository, _classifier = make_robot([reference], supersede_existing=True)

        _run_loop_assert_batch_processed(robot)
        (lde,) = _submitted_enhancements(repository)
        assert lde.supersedes == survivor.id

    def test_duplicate_live_lde_supersedes_the_first_listed(self, make_robot: RobotFactory) -> None:
        """Two competing live LDEs (inconsistent ODS): current behaviour picks the first one and starts a chain there."""
        reference = _make_reference()
        first, second = _make_lde(reference.id), _make_lde(reference.id)
        reference.enhancements = [first, second]

        robot, repository, _classifier = make_robot([reference], supersede_existing=True)

        _run_loop_assert_batch_processed(robot)
        (lde,) = _submitted_enhancements(repository)
        assert lde.supersedes == first.id

    def test_enhancements_for_a_different_reference_are_ignored(self, make_robot: RobotFactory) -> None:
        reference = _make_reference()
        foreign_reference_enh = _make_lde(uuid4())  # same source, but attached to another reference
        reference.enhancements = [foreign_reference_enh]

        robot, repository, classifier = make_robot([reference], supersede_existing=True)

        _run_loop_assert_batch_processed(robot)
        assert classifier.classified == [reference.id]
        (lde,) = _submitted_enhancements(repository)
        assert lde.supersedes is None


# --------------------------------------------------------------------------- batch handling


class TestBatchHandling:
    """Routing across a mixed batch, and empty-batch short circuits."""

    def test_mixed_batch_routes_each_reference_to_the_right_case(self, make_robot: RobotFactory) -> None:
        excluded_bare = _make_reference()
        excluded_bare.enhancements = [_make_inclusion(excluded_bare.id, value=False)]

        excluded_taxonomised = _make_reference()
        old_taxonomy = _make_lde(excluded_taxonomised.id)
        excluded_taxonomised.enhancements = [_make_inclusion(excluded_taxonomised.id, value=False), old_taxonomy]

        fresh = _make_reference()

        taxonomised = _make_reference()
        live_taxonomy = _make_lde(taxonomised.id)
        taxonomised.enhancements = [_make_inclusion(taxonomised.id, value=True), live_taxonomy]

        robot, repository, classifier = make_robot(
            [excluded_bare, excluded_taxonomised, fresh, taxonomised],
            supersede_existing=True,
        )

        _run_loop_assert_batch_processed(robot)
        # Only cases 3 and 4-when-enabled get classified.
        assert sorted(classifier.classified) == sorted([fresh.id, taxonomised.id])

        enhancements = _submitted_enhancements(repository)
        assert len(enhancements) == 3

        tombstones = [e for e in enhancements if isinstance(e.content, TombstoneEnhancement)]
        new_lde = [e for e in enhancements if e.reference_id == fresh.id]
        superseded_lde = [e for e in enhancements if e.reference_id == taxonomised.id]

        assert len(tombstones) == 1
        assert tombstones[0].supersedes == old_taxonomy.id
        assert len(new_lde) == 1
        assert new_lde[0].supersedes is None
        assert len(superseded_lde) == 1
        assert superseded_lde[0].supersedes == live_taxonomy.id
        assert _submitted_errors(repository) == []

    def test_no_batch_returns_false_without_submitting(self, make_robot: RobotFactory) -> None:
        robot, repository, classifier = make_robot([], empty_batch_info=True)

        assert asyncio.run(robot._loop_task()) is False  # noqa: SLF001
        assert repository.submitted is None
        assert repository.submit_calls == 0
        assert classifier.classified == []

    def test_batch_with_empty_reference_list_returns_false_without_submitting(self, make_robot: RobotFactory) -> None:
        robot, repository, classifier = make_robot([])

        assert asyncio.run(robot._loop_task()) is False  # noqa: SLF001
        assert repository.submitted is None
        assert repository.submit_calls == 0
        assert classifier.classified == []

"""Taxonomy annotation robot."""

import asyncio
from uuid import UUID

from destiny_sdk.enhancements import AnnotationEnhancement, BooleanAnnotation, Enhancement, LinkedDataEnhancement, TombstoneEnhancement
from destiny_sdk.references import Reference
from destiny_sdk.robots import EnhancementResultEntry, LinkedRobotError, RobotAutomationIn
from tenacity import AsyncRetrying, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from app.enhancements import build_linked_data_enhancement
from app.extractor import InvalidDocumentError, TaxonomyExtractor
from app.util import Runner, get_title_abstract_from_reference

NON_RETRYABLE_ERRORS = (InvalidDocumentError,)


class TaxonomyRobot(Runner):
    """Runner for taxonomy annotation."""

    NAME = "Climate and health taxonomy annotation robot"

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.extractor = TaxonomyExtractor(self.settings)
        self._retrying = AsyncRetrying(
            retry=retry_if_not_exception_type(NON_RETRYABLE_ERRORS),
            stop=stop_after_attempt(self.settings.llm_num_retries + 1),
            wait=wait_exponential(multiplier=1, max=30),
            reraise=True,
        )

    def _automation_query(self) -> RobotAutomationIn:
        return RobotAutomationIn(
            robot_id=self.settings.robot_id,
            query={
                "bool": {
                    "must": [
                        {"term": {"changeset.enhancements.content.annotations.scheme": self.settings.upstream_scheme}},
                        {"term": {"changeset.enhancements.content.annotations.label": self.settings.upstream_label}},
                    ]
                }
            },
        )

    async def _annotate_reference(self, reference: Reference) -> list[str]:
        """Return applied concept URIs for one reference."""
        title, abstract = get_title_abstract_from_reference(reference)
        concept_uris: list[str] = await self._retrying(self.extractor.extract, title=title, abstract=abstract)
        return concept_uris

    def _existing_taxonomy_annotation(self, reference: Reference) -> Enhancement | None:
        """Return the latest existing applicable taxonomy enhancement."""
        if not reference.enhancements:
            return None

        superseded_ids: set[UUID] = {enhancement.supersedes for enhancement in reference.enhancements if enhancement.supersedes is not None}

        for enhancement in reference.enhancements:
            # Search for any LDE this robot produced independent of robot version or vocab/context versions (URIs)
            if (
                isinstance(enhancement.content, LinkedDataEnhancement)
                and enhancement.reference_id == reference.id
                and enhancement.source == self.NAME
                and enhancement.id not in superseded_ids
            ):
                # Note: We are assuming that there is exactly one relevant non-superseded taxonomy enhancement.
                #       Should the ODS be in an inconsistent state, multiple runs may result in starting/continuing another supersession chain
                return enhancement

        return None

    def _is_not_included(self, reference: Reference) -> Enhancement | None:
        """Return value of the latest applicable inclusion label."""
        if not reference.enhancements:
            return None

        superseded_ids: set[UUID] = {enhancement.supersedes for enhancement in reference.enhancements if enhancement.supersedes is not None}

        for enhancement in reference.enhancements:
            if (
                isinstance(enhancement.content, AnnotationEnhancement) and enhancement.reference_id == reference.id and enhancement.id not in superseded_ids
                # Optional: `and enhancement.source in self.settings.upstream_source` (check robot name)
            ):
                for annotation in enhancement.content.annotations:
                    if (
                        isinstance(annotation, BooleanAnnotation)
                        and annotation.scheme == self.settings.upstream_scheme
                        and annotation.label == self.settings.upstream_label
                        and annotation.value == False  # noqa: E712
                    ):
                        # Note: We are assuming that there is exactly one relevant non-superseded inclusion enhancement.
                        return enhancement

        return None

    async def _loop_task(self) -> bool:
        """
        Poll, annotate, and submit one batch.

        Overview of exclusion and supersession cases:
          1) ID     in exclude AND no existing taxonomy -> do nothing
          2) ID     in exclude AND    existing taxonomy -> Create TombstoneEnhancement with supersession
          3) ID not in exclude AND no existing taxonomy -> Classify and write new LDE without
          4) ID not in exclude AND    existing taxonomy -> if settings.supersede_existing:
                                                              True: Classify and write LDE with supersession
                                                              False: do nothing
        """
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None or len(references) == 0:
            self.loop_logger.debug("No batches available")
            return False

        results: dict[UUID, list[str]] = {}
        permanent: dict[UUID, str] = {}
        failed: dict[UUID, str] = {}

        entries: list[EnhancementResultEntry] = []

        with self.tracer.start_as_current_span("taxonomy.batch") as span:
            span.set_attribute("app.reference.count", len(references))

            # Find all references that now have `inclusion == False`
            non_relevant = {
                reference.id: f"Reference is classified as not relevant by {enhancement.id}"
                for reference in references
                if (enhancement := self._is_not_included(reference)) is not None
            }

            # Check reference for existing taxonomy annotation that we would need to supersede
            has_existing_annotation = {
                reference.id: enhancement.id  # Remember enhancement_id to be superseded per reference
                for reference in references
                if (enhancement := self._existing_taxonomy_annotation(reference)) is not None
            }

            skip_classification = non_relevant.keys() if self.settings.supersede_existing else (non_relevant.keys() | has_existing_annotation.keys())
            needs_retirement = non_relevant.keys() & has_existing_annotation

            # Classify all references that are not excluded or already have a taxonomy classification
            outcomes = await asyncio.gather(
                *(self._annotate_reference(reference) for reference in references if reference.id not in skip_classification),
                return_exceptions=True,
            )

            # Collect results (classifications or error messages)
            for reference, outcome in zip(
                (reference for reference in references if reference.id not in skip_classification),
                outcomes,
                strict=True,
            ):
                if not isinstance(outcome, BaseException):
                    results[reference.id] = outcome
                elif isinstance(outcome, NON_RETRYABLE_ERRORS):
                    permanent[reference.id] = f"{type(outcome).__name__}: {outcome}"
                else:
                    failed[reference.id] = f"{type(outcome).__name__}: {outcome}"

            # Stop runner if too many records failed
            if len(failed) > self.settings.abandon_threshold * len(references):
                self.loop_logger.critical(f"{len(failed)}/{len(references)} failed after retries; endpoint looks down, shutting down.")
                await self.stop()
                return False

            # Log statistics to OpenTelemetry
            span.set_attributes(
                {
                    "app.taxonomy_annotated": len(results),
                    "app.taxonomy_permanent_failures": len(permanent),
                    "app.taxonomy_failed": len(failed),
                    "app.taxonomy_skipped": len(skip_classification),
                    "app.taxonomy_non_relevant": len(non_relevant),
                    "app.taxonomy_had_annotation": len(has_existing_annotation),
                    "app.taxonomy_needs_retirement": needs_retirement,
                },
            )

        # Handle case (2) ID in exclude AND existing taxonomy -> Create TombstoneEnhancement
        entries += [
            Enhancement(
                reference_id=reference_id,
                source=self.NAME,
                visibility=self.settings.enhancement_visibility,
                robot_version=self.settings.robot_version,
                supersedes=has_existing_annotation[reference_id],
                content=TombstoneEnhancement(reason=non_relevant.get(reference_id, "Taxonomy classification became obsolete")),
            )
            for reference_id in needs_retirement
        ]

        # Handle case (3) ID not in exclude AND no existing taxonomy -> Classify and write new LDE with supersession
        entries += [
            Enhancement(
                reference_id=reference_id,
                source=self.NAME,
                visibility=self.settings.enhancement_visibility,
                robot_version=self.settings.robot_version,
                supersedes=has_existing_annotation.get(reference_id),
                content=build_linked_data_enhancement(concept_uris=uris, vocabulary_uri=self.settings.vocabulary_uri, context_uri=self.settings.context_uri),
            )
            for reference_id, uris in results.items()
        ]

        # Handle case (3) where classification failed
        failures = {**permanent, **failed}
        entries += [LinkedRobotError(reference_id=reference_id, message=message) for reference_id, message in failures.items()]

        # Send enhancements and errors to repository
        await self.repository.submit_enhancements(batch_info=batch_info, enhancements=entries)

        self.loop_logger.info(
            f"[Total: {self.total_entries_processed:,} entries] Submitted {len(results):,} enhancements, "
            f"{len(failures):,} failures ({len(permanent):,} permanent, {len(failed):,} failed-after-retries), "
            f"{len(non_relevant):,} excluded, {len(has_existing_annotation):,} superseded, {len(needs_retirement):,} retired.",
        )

        return True

"""Taxonomy annotation robot."""

import asyncio
from uuid import UUID

from destiny_sdk.enhancements import Enhancement
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
                        {"term": {"changeset.enhancements.content.annotations.value": True}},
                    ]
                }
            },
        )

    async def _annotate_reference(self, reference: Reference) -> list[str]:
        """Return applied concept URIs for one reference."""
        title, abstract = get_title_abstract_from_reference(reference)
        concept_uris: list[str] = await self._retrying(self.extractor.extract, title=title, abstract=abstract)
        return concept_uris

    async def _loop_task(self) -> bool:
        """Poll, annotate, and submit one batch."""
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return False

        results: dict[UUID, list[str]] = {}
        permanent: dict[UUID, str] = {}
        failed: dict[UUID, str] = {}

        with self.tracer.start_as_current_span("taxonomy.batch") as span:
            span.set_attribute("app.reference.count", len(references))

            outcomes = await asyncio.gather(*(self._annotate_reference(reference) for reference in references), return_exceptions=True)
            for reference, outcome in zip(references, outcomes, strict=True):
                if not isinstance(outcome, BaseException):
                    results[reference.id] = outcome
                elif isinstance(outcome, NON_RETRYABLE_ERRORS):
                    permanent[reference.id] = f"{type(outcome).__name__}: {outcome}"
                else:
                    failed[reference.id] = f"{type(outcome).__name__}: {outcome}"

            if len(failed) > self.settings.abandon_threshold * len(references):
                self.loop_logger.critical(f"{len(failed)}/{len(references)} failed after retries; endpoint looks down, shutting down.")
                await self.stop()
                return False

            span.set_attributes(
                {
                    "app.taxonomy_annotated": len(results),
                    "app.taxonomy_permanent_failures": len(permanent),
                    "app.taxonomy_failed": len(failed),
                }
            )

        failures = {**permanent, **failed}
        entries: list[EnhancementResultEntry] = [
            Enhancement(
                reference_id=reference_id,
                source=self.NAME,
                visibility=self.settings.enhancement_visibility,
                robot_version=self.settings.robot_version,
                content=build_linked_data_enhancement(concept_uris=uris, vocabulary_uri=self.settings.vocabulary_uri, context_uri=self.settings.context_uri),
            )
            for reference_id, uris in results.items()
        ]
        entries += [LinkedRobotError(reference_id=reference_id, message=message) for reference_id, message in failures.items()]

        await self.repository.submit_enhancements(batch_info=batch_info, enhancements=entries)

        self.loop_logger.info(
            f"[Total: {self.total_entries_processed:,} entries] Submitted {len(results):,} enhancements, "
            f"{len(failures):,} failures ({len(permanent):,} permanent, {len(failed):,} failed-after-retries)"
        )

        return True

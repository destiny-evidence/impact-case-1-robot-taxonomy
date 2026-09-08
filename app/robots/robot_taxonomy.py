"""Taxonomy annotation robot."""

import asyncio
from uuid import UUID

from destiny_sdk.enhancements import Enhancement
from destiny_sdk.references import Reference
from destiny_sdk.robots import EnhancementResultEntry, RobotAutomationIn
from opentelemetry import trace

from app.enhancements import build_linked_data_enhancement
from app.extractor import TaxonomyExtractor
from app.util import Runner, get_title_abstract_from_reference


class TaxonomyRobot(Runner):
    """Runner for taxonomy annotation."""

    NAME = "Climate and health taxonomy annotation robot"

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.extractor = TaxonomyExtractor(self.settings)

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
        if abstract is None:
            return []
        return await self.extractor.extract(title=title, abstract=abstract)

    async def _loop_task(self) -> bool:
        """Poll, annotate, and submit one batch."""
        batch_info, references = await self.repository.get_next_batch()

        if batch_info is None or references is None:
            self.loop_logger.debug("No batches available")
            return False

        results: dict[UUID, list[str]] = {}

        with self.tracer.start_as_current_span("taxonomy.batch") as span:
            span.set_attribute("app.reference.count", len(references))

            outcomes = await asyncio.gather(*(self._annotate_reference(reference) for reference in references), return_exceptions=True)
            for reference, outcome in zip(references, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    self.loop_logger.error(f"Abandoning batch {batch_info.id}for redelivery: {outcome}")
                    span.set_status(trace.StatusCode.ERROR, f"abandoned for redelivery: {outcome}")
                    return False
                results[reference.id] = outcome

            span.set_attribute("app.taxonomy_annotated", len(results))

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

        await self.repository.submit_enhancements(batch_info=batch_info, enhancements=entries)

        self.loop_logger.info(f"[Total: {self.total_entries_processed:,} entries] Submitted {len(entries):,} ")

        return True

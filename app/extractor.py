"""deet extraction wrapper: TTL-sourced prompts, CSV-pinned attribute identity."""

import asyncio
import csv
from pathlib import Path

from app.util.config import Settings


class TaxonomyExtractor:
    """
    Runs deet flat LLM extraction and maps positive concepts to their URIs.

    Prompts come from the TTL (the source of truth for wording); the attribute
    set and ids are pinned to the frozen prompts_used.csv. A mismatch between
    them is drift and fails hard at startup.
    """

    def __init__(self, settings: Settings) -> None:
        from deet.data_models.base import Attribute, AttributeType
        from deet.data_models.taxonomy import load_schemes_from_ttl
        from deet.extractors.base_extractor import DataExtractionConfig
        from deet.extractors.extractor_registry import get_data_extractor

        bundle = settings.extraction_config.parent
        config = DataExtractionConfig.from_yaml(settings.extraction_config)

        # Prompt text from the TTL (basename in the frozen config -> bundle dir).
        ttl_path = bundle / Path(config.vocabulary_path).name
        ttl_by_concept: dict[str, tuple[str, str, str]] = {}
        for scheme in load_schemes_from_ttl(ttl_path):
            for concept in scheme.concepts.values():
                ttl_by_concept[concept.identifier] = (
                    concept.build_prompt(config.vocab_prompt_locations),
                    concept.uri,
                    concept.pref_label,
                )

        # Attribute identity from the frozen CSV; reconcile against the TTL.
        canonical = list(csv.DictReader((bundle / "prompts_used.csv").open()))
        csv_ids = {row["concept_id"] for row in canonical}
        ttl_ids = set(ttl_by_concept)
        if csv_ids != ttl_ids:
            missing, extra = sorted(csv_ids - ttl_ids), sorted(ttl_ids - csv_ids)
            raise RuntimeError(
                f"Frozen attribute set drifted from the TTL. "
                f"In CSV but not TTL: {missing}. In TTL but not CSV: {extra}. "
                f"Re-run export_config_to_robot.py against the current TTL."
            )

        self._attributes = []
        self._uri_by_attr_id: dict[int, str] = {}
        for row in canonical:
            prompt, uri, label = ttl_by_concept[row["concept_id"]]
            attr_id = int(row["attribute_id"])
            self._attributes.append(
                Attribute(
                    prompt=prompt,
                    output_data_type=AttributeType.BOOL,
                    attribute_id=attr_id,
                    attribute_label=label,
                    concept_id=row["concept_id"],
                )
            )
            self._uri_by_attr_id[attr_id] = uri

        self._extractor = get_data_extractor(config)

    async def extract(self, title: str | None, abstract: str | None) -> list[str]:
        """Return the concept URIs the model marks as applying to this reference."""
        text = f"# {title or ''}\n\n{abstract or ''}"
        result = await asyncio.to_thread(self._extractor.extract_from_document, self._attributes, payload=text)
        return [self._uri_by_attr_id[a.attribute.attribute_id] for a in result.annotations if a.output_data is True]

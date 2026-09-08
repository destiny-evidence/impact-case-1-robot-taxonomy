"""deet extraction wrapper: TTL-sourced prompts, CSV-pinned attribute identity."""

import asyncio
import csv
from pathlib import Path

from deet.data_models.base import Attribute
from deet.data_models.taxonomy import Concept, ConceptScheme, MappedConceptScheme

from app.util.config import Settings


class TaxonomyExtractor:
    """
    Runs deet flat LLM extraction and maps positive concepts to their URIs.

    Prompts come from the TTL (the source of truth for wording); the attribute
    set and ids are pinned to the frozen prompts_used.csv. A mismatch between
    them is drift and fails hard at startup.
    """

    def __init__(self, settings: Settings) -> None:
        from deet.data_models.taxonomy import load_schemes_from_ttl
        from deet.extractors.base_extractor import DataExtractionConfig
        from deet.extractors.extractor_registry import get_data_extractor
        from deet.extractors.hierarchical.base import VocabularyLLMExtractor

        config = DataExtractionConfig.from_yaml(settings.extraction_config)
        if not config.vocabulary_path:
            raise ValueError("Config does not supply a vocabulary path")
        config_dir = settings.extraction_config.parent
        vocabulary_ttl = config_dir / config.vocabulary_path.name

        csv_attributes = self._load_csv_attributes(settings.extraction_attribute_csv)
        schemes = load_schemes_from_ttl(vocabulary_ttl)
        mapped_schemes = self._build_mapped_schemes(schemes, csv_attributes)

        self._attributes = [concept.attribute for ms in mapped_schemes for concept in ms.concepts.values()]

        self._uri_by_attr_id = {concept.attribute.attribute_id: concept.uri for ms in mapped_schemes for concept in ms.concepts.values()}

        extractor = get_data_extractor(config)
        if isinstance(extractor, VocabularyLLMExtractor):
            extractor.mapped_schemes = mapped_schemes
        self._extractor = extractor

    def _load_csv_attributes(self, csv_path: Path) -> list[Attribute]:
        from deet.data_models.base import Attribute, AttributeType

        rows = list(csv.DictReader(csv_path.open()))
        return [
            Attribute(
                prompt=row["prompt"],
                output_data_type=AttributeType.BOOL,
                attribute_id=int(row["attribute_id"]),
                attribute_label=row["attribute_label"],
                concept_id=row["concept_id"],
            )
            for row in rows
        ]

    def _build_mapped_schemes(self, schemes: list[ConceptScheme[Concept]], attributes: list[Attribute]) -> list[MappedConceptScheme]:
        from deet.data_models.taxonomy import MappedConcept, MappedConceptScheme

        attr_by_concept_id = {attr.concept_id: attr for attr in attributes if attr.concept_id is not None}
        mapped_schemes: list[MappedConceptScheme] = []
        for scheme in schemes:
            mapped_concepts = {}
            for concept_id, concept in scheme.concepts.items():
                attr = attr_by_concept_id.get(concept_id)
                if attr is None:
                    raise ValueError(f"Concept {concept} does not exist in attributes.")

                mapped_concepts[concept_id] = MappedConcept(**concept.model_dump(), attribute=attr)
            mapped_schemes.append(
                MappedConceptScheme(
                    title=scheme.title, description=scheme.description, uri=scheme.uri, top_concepts=scheme.top_concepts, concepts=mapped_concepts
                )
            )
        return mapped_schemes

    async def extract(self, title: str | None, abstract: str | None) -> list[str]:
        """Return the concept URIs the model marks as applying to this reference."""
        text = f"# {title or ''}\n\n{abstract or ''}"
        result = await asyncio.to_thread(self._extractor.extract_from_document, self._attributes, payload=text)
        return [self._uri_by_attr_id[a.attribute.attribute_id] for a in result.annotations if a.output_data is True]

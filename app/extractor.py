"""deet extraction wrapper: TTL-sourced prompts, CSV-pinned attribute identity."""

import asyncio
import csv
import functools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from deet.data_models.base import Attribute, AttributeType
from deet.data_models.taxonomy import (
    Concept,
    ConceptScheme,
    MappedConcept,
    MappedConceptScheme,
    load_schemes_from_ttl,
)
from deet.extractors.base_extractor import DataExtractionConfig, ExtractionMethod
from deet.extractors.extractor_registry import get_data_extractor
from deet.extractors.hierarchical.base import VocabularyLLMExtractor
from deet.utils.tokenisation import count_tokens

from app.util.config import Settings
from app.util.util import RateLimiter


class InvalidDocumentError(Exception):
    """Document is outside the valid token range (missing/too short, or too long)."""


class TaxonomyExtractor:
    """
    Runs deet flat LLM extraction and maps positive concepts to their URIs.

    Prompts come from the TTL (the source of truth for wording); the attribute
    set and ids are pinned to the frozen prompts_used.csv. A mismatch between
    them is drift and fails hard at startup.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._rpm_limiter = RateLimiter(rate=settings.llm_requests_per_minute)
        self._tpm_limiter = RateLimiter(rate=settings.llm_tokens_per_minute)
        self._pool = ThreadPoolExecutor(max_workers=settings.llm_max_concurrent_extractions, thread_name_prefix="extract")

        config = DataExtractionConfig.from_yaml(settings.extraction_config)
        if not config.vocabulary_path:
            raise ValueError("Config does not supply a vocabulary path")
        config_dir = settings.extraction_config.parent
        vocabulary_ttl = config_dir / config.vocabulary_path.name

        csv_attributes = self._load_csv_attributes(settings.extraction_attribute_csv)
        schemes = load_schemes_from_ttl(vocabulary_ttl)
        mapped_schemes = self._build_mapped_schemes(schemes, csv_attributes)

        self._max_requests = self._compute_max_requests(config=config, mapped_schemes=mapped_schemes)

        self._attributes = [concept.attribute for ms in mapped_schemes for concept in ms.concepts.values()]

        self._uri_by_attr_id = {concept.attribute.attribute_id: concept.uri for ms in mapped_schemes for concept in ms.concepts.values()}

        if config.max_tokens is None:
            raise ValueError("Set max_tokens in the extraction config so output is bounded")
        self._prompt_tokens = int(sum(count_tokens(config.model, attr.prompt or "") for attr in self._attributes))
        self._system_tokens = int(count_tokens(config.model, str(config.prompt_config.system_prompt)))

        extractor = get_data_extractor(config)
        if isinstance(extractor, VocabularyLLMExtractor):
            extractor.mapped_schemes = mapped_schemes
        self._extractor = extractor
        self._config = config

    def _load_csv_attributes(self, csv_path: Path) -> list[Attribute]:
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

    def _compute_max_requests(self, config: DataExtractionConfig, mapped_schemes: list[MappedConceptScheme]) -> int:
        """Compute the maximum possible requests made per call to extract_from_document."""
        if config.method == ExtractionMethod.HIERARCHICAL_TOP_DOWN:
            return sum(self._scheme_depth(s) for s in mapped_schemes)
        return 1

    def _reserved_tokens(self, doc_tokens: int) -> int:
        """Tokens the cascade over this document is expected to use."""
        per_call = self._system_tokens + doc_tokens + self._settings.llm_expected_output_tokens
        return self._prompt_tokens + self._max_requests * per_call

    def _scheme_depth(self, scheme: ConceptScheme) -> int:
        """Calculate number of levels the top down loop runs = longest root->leaf chain."""

        def depth_from(concept: Concept) -> int:
            children = scheme.narrower(concept.identifier)
            return 1 + max((depth_from(c) for c in children), default=0)

        return max((depth_from(root) for root in scheme.roots), default=0)

    def _validate_text_tokens(self, text: str) -> int:
        """Validate the document text is within the configured token range, returning its size."""
        token_count = int(count_tokens(self._config.model, text))
        if token_count < self._settings.min_document_tokens:
            raise InvalidDocumentError(f"Document has {token_count} tokens, under the {self._settings.min_document_tokens} minimum")
        if token_count > self._settings.max_document_tokens:
            raise InvalidDocumentError(f"Document has {token_count} tokens, over the {self._settings.max_document_tokens} maximum")
        return token_count

    async def extract(self, title: str | None, abstract: str | None) -> list[str]:
        """Return the concept URIs the model marks as applying to this reference."""
        text = f"# {title or ''}\n\n{abstract or ''}"
        max_tokens = self._reserved_tokens(self._validate_text_tokens(text))

        await self._rpm_limiter.acquire(self._max_requests)
        await self._tpm_limiter.acquire(max_tokens)
        num_requests = 0
        num_tokens = 0
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                self._pool,
                functools.partial(self._extractor.extract_from_document, self._attributes, payload=text),
            )
            num_requests = sum(1 for m in result.messages if m.get("role") == "system")
            num_tokens = result.input_tokens + result.output_tokens
            return [self._uri_by_attr_id[a.attribute.attribute_id] for a in result.annotations if a.output_data is True]
        finally:
            await self._rpm_limiter.release(self._max_requests - num_requests)
            await self._tpm_limiter.release(max_tokens - num_tokens)

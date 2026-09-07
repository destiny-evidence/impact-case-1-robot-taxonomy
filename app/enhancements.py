"""Build DESTINY LinkedDataEnhancement content from applied taxonomy concepts."""

from typing import cast

from destiny_sdk.enhancements import LinkedDataEnhancement
from pydantic import HttpUrl, JsonValue


def build_linked_data_enhancement(
    concept_uris: list[str],
    *,
    vocabulary_uri: HttpUrl,
    context_uri: str,
) -> LinkedDataEnhancement:
    """Wrap applied concept URIs as a single-investigation JSON-LD enhancement."""
    return LinkedDataEnhancement(
        vocabulary_uri=vocabulary_uri,
        data=cast(
            "dict[str, JsonValue]",
            {
                "@context": context_uri,
                "@type": "LinkedDataEnhancement",
                "hasInvestigation": {
                    "@type": "Investigation",
                    "hasAppliedConcept": concept_uris,
                },
            },
        ),
    )

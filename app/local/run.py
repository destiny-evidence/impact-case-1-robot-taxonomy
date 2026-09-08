"""Command-line entry points for the taxonomy robot."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="DESTinY IC1 taxonomy robot.")


@app.command("try-extract")
def try_extract(
    file: Annotated[Path, typer.Argument(help="Text file, one document per line.")],
    limit: Annotated[int, typer.Option(help="Only process the first N non-empty lines.")] = 3,
) -> None:
    """Run the extractor locally on raw lines of text (no repository, no runner)."""
    from app.enhancements import build_linked_data_enhancement
    from app.extractor import TaxonomyExtractor
    from app.util.config import get_settings

    settings = get_settings()

    lines = [ln.strip() for ln in file.read_text().splitlines() if ln.strip()][:limit]
    typer.echo(f"Loaded {len(lines)} document(s); building extractor...")

    extractor = TaxonomyExtractor(settings)

    async def _run() -> None:
        for i, text in enumerate(lines):
            uris = await extractor.extract(title=None, abstract=text)
            typer.secho(f"\n[{i}] {len(uris)} concept(s) applied:", fg=typer.colors.GREEN)
            enhancement = build_linked_data_enhancement(concept_uris=uris, vocabulary_uri=settings.vocabulary_uri, context_uri=settings.context_uri)
            typer.secho(f"\n[{i}] {len(uris)} concept(s) applied:", fg=typer.colors.GREEN)
            typer.echo(json.dumps(enhancement.data, indent=2))

    asyncio.run(_run())


def run() -> None:
    """Console-script entry point (`local-robot`)."""
    app()

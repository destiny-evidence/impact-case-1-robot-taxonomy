"""Command-line entry points for the taxonomy robot."""

import asyncio
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
    from app.extractor import TaxonomyExtractor
    from app.util.config import get_settings

    lines = [ln.strip() for ln in file.read_text().splitlines() if ln.strip()][:limit]
    typer.echo(f"Loaded {len(lines)} document(s); building extractor...")

    extractor = TaxonomyExtractor(get_settings())

    async def _run() -> None:
        for i, text in enumerate(lines):
            uris = await extractor.extract(title=None, abstract=text)
            typer.secho(f"\n[{i}] {len(uris)} concept(s) applied:", fg=typer.colors.GREEN)
            for uri in uris:
                typer.echo(f"    {uri}")

    asyncio.run(_run())


def run() -> None:
    """Console-script entry point (`local-robot`)."""
    app()

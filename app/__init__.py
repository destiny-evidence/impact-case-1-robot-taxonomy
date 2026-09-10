"""Main robot entry point."""

import asyncio

from app.robots import TaxonomyRobot


def run() -> None:
    """Console script entry point {'robot'}."""

    async def _main() -> None:
        runner = TaxonomyRobot(name="taxonomy")
        await runner.start()

    asyncio.run(_main())


__all__ = ["run"]

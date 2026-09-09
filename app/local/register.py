"""Register this robot with the repository: forward automation, backward backfill, or both."""

import typer


def register(  # noqa: PLR0913
    forward: bool = True,
    backward: bool = True,
    automation_endpoint: str = "/enhancement-requests/automations/",
    backfill_endpoint: str = "/enhancement-requests/",
    dry_run: bool = False,
    limit: int = 100,
) -> None:
    """Wire this robot to the repository. Both directions by default; --no-forward / --no-backward to pick one."""
    from destiny_sdk.client import OAuthClient
    from destiny_sdk.robots import EnhancementRequestIn

    from app.robots.robot_taxonomy import TaxonomyRobot

    robot = TaxonomyRobot(name="taxonomy")
    settings = robot.settings
    client = OAuthClient(env=settings.env.value)

    # Forward: register the percolator automation so future matches auto-route to the robot.
    if forward:
        automation = robot._automation_query()  # noqa: SLF001
        typer.echo(automation.model_dump_json(indent=2))
        if not dry_run:
            resp = client.get_client().post(automation_endpoint, json=automation.model_dump(mode="json"))
            if resp.status_code == 409:  # noqa: PLR2004
                typer.secho("Automation already registered", fg=typer.colors.YELLOW)
            else:
                resp.raise_for_status()
                typer.secho(f"Automation registered: {resp.json().get('id')}", fg=typer.colors.GREEN)

    # Backward: request enhancement for existing references matching the same criteria.
    if backward:
        annotation = f"{settings.upstream_scheme}/{settings.upstream_label}"
        reference_ids: list[str] = []
        page = 1
        while True:
            batch = client.search(query="*", annotations=[annotation], page=page).model_dump()["references"]
            if not batch:
                break
            batch_ids = [r["id"] for r in batch]
            reference_ids += batch_ids
            if len(reference_ids) > limit:
                break
            page += 1
            typer.echo(f"{len(reference_ids)} existing references match {annotation} so far")
            if not dry_run and batch_ids:
                body = EnhancementRequestIn(reference_ids=batch_ids, robot_id=settings.robot_id).model_dump(mode="json")
                resp = client.get_client().post(backfill_endpoint, json=body)
                resp.raise_for_status()
                typer.secho(f"Backfill requested for {len(batch_ids)} references", fg=typer.colors.GREEN)


def run() -> None:
    typer.run(register)

# Taxonomy robot for DESTinY's climate and health map (impact case 1)

This repository hosts the code to annotate references with concepts from a taxonomy using pipelines developed and evaluated
in [the project-specific repository](https://github.com/destiny-evidence/impact-case-1).

Currently, we use [deet](https://github.com/destiny-evidence/data-extraction-evaluation-toolkit) to develop pipelines for taxonomy annotation.
This robot takes deet configuration artefacts (see `.config/taxonomy`, and uses deet)
to run extraction _as defined by those artefacts_ on references, returning linked data enhancements.

## Configuration

All configuration is via environment variables — see `app/util/config.py`
for the full list, types, and defaults.

### Local Development

Copy the template and fill in the required values:

```bash
cp .configs/.env.example .env
```

Then run

```bash
uv run local-robot texts.txt  # Try the extractor on a file of abstracts
uv run robot                  # Run the full polling robot
```

Both the robot's own settings and deet read this `.env` directly.

### Production

The same variables are injected as environment variables by terraform config.

### Required values (no defaults)

| Variable                               | Source                                             |
| -------------------------------------- | -------------------------------------------------- |
| `ROBOT_ID`, `ROBOT_SECRET`             | Registering the robot with the DESTINY repository  |
| `AZURE_API_KEY`, `AZURE_API_BASE`      | The Azure LLM deployment                           |
| `VOCABULARY_UID`, `VOCABULARY_VERSION` | The vocabulary published in the Vocabulary Builder |

Everything else (rate limits, batch size, document bounds, retry/abandon
behaviour, …) has a sensible default in `config.py` and only needs setting to
override it.

### Running a robot

First, [register the robot](https://destiny-evidence.github.io/destiny-repository/procedures/robot-registration.html).

## Observability

With `OTEL_ENABLED=true`, the robot exports OpenTelemetry traces to Honeycomb over OTLP/HTTP. The ingest key and
endpoint come from `OTEL_CONFIG`, a JSON blob (see `.configs/.env.example`); Terraform assembles it from the
`honeycomb_api_key` and `honeycomb_trace_endpoint` variables and injects it as a Container App secret.

The robot reports to its own Honeycomb dataset, named `destiny-taxonomy-robot-<env>` (e.g. `destiny-taxonomy-robot-staging`), set from the robot's task name and environment in `app/util/telemetry.py`.

## Development notes

### Build / test

```bash
# if needed, clear caches
pre-commit clear
# run checks on all files
pre-commit run --all-files
```

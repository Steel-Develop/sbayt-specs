# Sbayt Specs

Public registry of immutable contracts shared by Sbayt components. Contract
sources remain in their owner repositories; this repository contains only
artifacts produced by their publication flows.

## Layout

Model contracts use this layout:

```text
<namespace>/
  openapi.yaml  # OpenAPI 3.1 source copied from the owner repository
  schema.json   # Deterministic JSON Schema 2020-12 derivation
```

The repository tree and immutable tags are the contract catalog.

## Consumption

Consumers must reference an exact `<contract-id>-v<version>` tag. Branches,
`master`, and aliases such as `latest` are mutable and must not be used as
contract references.

```text
https://raw.githubusercontent.com/Steel-Develop/sbayt-specs/<contract-id>-v<version>/<namespace>/openapi.yaml
https://raw.githubusercontent.com/Steel-Develop/sbayt-specs/<contract-id>-v<version>/<namespace>/schema.json
```

Merging a validated publication pull request creates its immutable tag
automatically. Published tags are retained indefinitely and must never be moved,
overwritten, or deleted.

## Validation

CI discovers namespaces automatically and validates their structure, formats,
version consistency, derivation, and tag immutability. The same validation can
be run locally with:

```bash
uv run .github/scripts/validate_registry.py
```

## Ownership

Do not edit contracts directly in this repository. Changes originate in the
repository that owns the contract, where code and contract tests are reviewed
together. Its publication flow creates or reuses the immutable tag here.
Service-specific contracts remain with their services.

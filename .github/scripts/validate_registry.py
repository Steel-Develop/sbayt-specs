# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "jsonschema==4.26.0",
#   "openapi-spec-validator==0.9.0",
#   "PyYAML==6.0.3",
# ]
# ///

"""Validate the registry and publish missing immutable tags after merge."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

ARTIFACT_NAMES = {"openapi.yaml", "schema.json"}
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
RAW_BASE = "https://raw.githubusercontent.com/Steel-Develop/sbayt-specs/"


def require(condition: bool, error: str) -> None:
    if not condition:
        raise SystemExit(error)


def rewrite_refs(value: Any) -> Any:
    if isinstance(value, dict):
        rewritten = {}
        for key, item in value.items():
            if key == "$ref":
                prefix = "#/components/schemas/"
                require(
                    isinstance(item, str) and item.startswith(prefix),
                    f"UNSUPPORTED_PUBLIC_REF ref={item!r}",
                )
                component = item.removeprefix(prefix)
                require(
                    bool(component) and "/" not in component,
                    f"UNSUPPORTED_PUBLIC_REF ref={item!r}",
                )
                rewritten[key] = f"#/$defs/{component}"
            else:
                rewritten[key] = rewrite_refs(item)
        return rewritten
    if isinstance(value, list):
        return [rewrite_refs(item) for item in value]
    return value


def tag_exists(tag: str) -> bool:
    tag_ref = f"refs/tags/{tag}"
    status = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", tag_ref],
        check=False,
        capture_output=True,
    ).returncode
    require(status in {0, 1}, f"TAG_LOOKUP_FAILED tag={tag}")
    return status == 0


def validate_contract(directory: Path, existing_tags: set[str]) -> str:
    namespace = directory.name
    require(
        NAME_PATTERN.fullmatch(namespace) is not None,
        f"INVALID_CONTRACT_NAMESPACE value={namespace!r}",
    )
    entries = {path.name for path in directory.iterdir()}
    require(
        entries == ARTIFACT_NAMES,
        f"INVALID_CONTRACT_DIRECTORY namespace={namespace}",
    )

    openapi = yaml.safe_load((directory / "openapi.yaml").read_bytes())
    require(isinstance(openapi, dict), f"INVALID_OPENAPI namespace={namespace}")
    require(
        openapi.get("openapi") == "3.1.0" and openapi.get("paths") == {},
        f"INVALID_MODEL_CONTRACT namespace={namespace}",
    )
    rewrite_refs(openapi)
    validate(openapi)

    info = openapi.get("info")
    components = openapi.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    require(
        isinstance(schemas, dict),
        f"INVALID_OPENAPI namespace={namespace} missing_component_schemas",
    )
    require(
        isinstance(version, str) and SEMVER_PATTERN.fullmatch(version) is not None,
        f"INVALID_CONTRACT_VERSION namespace={namespace} value={version!r}",
    )

    schema = json.loads((directory / "schema.json").read_bytes())
    require(isinstance(schema, dict), f"INVALID_JSON_SCHEMA namespace={namespace}")
    Draft202012Validator.check_schema(schema)
    schema_id = schema.get("$id")
    schema_suffix = f"/{namespace}/schema.json"
    require(
        isinstance(schema_id, str)
        and schema_id.startswith(RAW_BASE)
        and schema_id.endswith(schema_suffix),
        f"INVALID_SCHEMA_ID namespace={namespace} value={schema_id!r}",
    )
    tag = schema_id[len(RAW_BASE) : -len(schema_suffix)]
    version_suffix = f"-v{version}"
    require(
        tag.endswith(version_suffix),
        f"TAG_VERSION_MISMATCH namespace={namespace} tag={tag}",
    )
    contract_id = tag[: -len(version_suffix)]
    require(
        NAME_PATTERN.fullmatch(contract_id) is not None,
        f"INVALID_CONTRACT_ID namespace={namespace} value={contract_id!r}",
    )
    require(tag not in existing_tags, f"DUPLICATE_CONTRACT_TAG tag={tag}")

    expected_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{RAW_BASE}{tag}{schema_suffix}",
        "title": info["title"],
        "$defs": rewrite_refs(schemas),
    }
    if isinstance(info.get("description"), str):
        expected_schema["description"] = info["description"]
    require(
        schema == expected_schema,
        f"DERIVED_SCHEMA_MISMATCH namespace={namespace}",
    )

    if tag_exists(tag):
        comparison = subprocess.run(
            ["git", "diff", "--quiet", f"refs/tags/{tag}", "--", namespace],
            check=False,
        ).returncode
        require(comparison == 0, f"IMMUTABLE_TAG_CONFLICT tag={tag}")

    return tag


def validate_registry(root: Path) -> set[str]:
    artifact_paths = {
        path
        for artifact_name in ARTIFACT_NAMES
        for path in root.glob(f"*/{artifact_name}")
    }
    contract_directories = {path.parent for path in artifact_paths}
    allowed_root_entries = {".git", ".github", "LICENSE", "README.md"} | {
        path.name for path in contract_directories
    }
    root_entries = {path.name for path in root.iterdir()}
    require(
        {"LICENSE", "README.md"} <= root_entries,
        "MISSING_REPOSITORY_FILE",
    )
    require(
        root_entries <= allowed_root_entries,
        f"UNEXPECTED_REPOSITORY_ENTRY entries={sorted(root_entries - allowed_root_entries)}",
    )

    contract_tags: set[str] = set()
    for directory in sorted(contract_directories):
        contract_tags.add(validate_contract(directory, contract_tags))

    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        current_tag = os.environ.get("GITHUB_REF_NAME")
        require(
            current_tag in contract_tags,
            f"TAG_VERSION_MISMATCH actual={current_tag!r}",
        )

    print(f"Validated {len(contract_tags)} contract(s).")
    return contract_tags


def publish_missing_tags(contract_tags: set[str]) -> None:
    require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_REF") == "refs/heads/master",
        "TAG_PUBLICATION_REQUIRES_MASTER",
    )
    missing_tags = [tag for tag in sorted(contract_tags) if not tag_exists(tag)]
    if not missing_tags:
        print("No contract tags need publication.")
        return

    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        check=True,
    )
    for tag in missing_tags:
        subprocess.run(
            ["git", "tag", "--annotate", tag, "--message", f"Publish {tag}"],
            check=True,
        )
        subprocess.run(["git", "push", "origin", f"refs/tags/{tag}"], check=True)
        print(f"Published {tag}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish-tags", action="store_true")
    arguments = parser.parse_args()
    contract_tags = validate_registry(Path.cwd())
    if arguments.publish_tags:
        publish_missing_tags(contract_tags)


if __name__ == "__main__":
    main()

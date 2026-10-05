#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

GRAPHQL_URL = "https://api.github.com/graphql"
API_VERSION = "2026-03-10"
OWNER = "riley-berg"
REPO = "riley-berg/PASI"
PROJECT_NUMBER = 5
EXPECTED_VIEW_NAME = "PASI Roadmap"
REQUIRED_DATE_FIELDS = {"Start date", "Target date"}
CANONICAL_NUMBERS = [1, *range(19, 36)]

PROJECT_QUERY = """
query Project($login: String!, $number: Int!) {
  user(login: $login) {
    projectV2(number: $number) {
      id
      number
      title
      url
      fields(first: 100) {
        nodes {
          __typename
          ... on ProjectV2Field {
            id
            name
            dataType
          }
          ... on ProjectV2IterationField {
            id
            name
          }
        }
      }
      items(first: 100) {
        nodes {
          content {
            __typename
            ... on Issue {
              number
              repository { nameWithOwner }
            }
          }
          fieldValues(first: 100) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldDateValue {
                date
                field { name }
              }
            }
          }
        }
      }
      views(first: 20) {
        nodes {
          number
          name
          layout
        }
      }
    }
  }
}
"""


def token() -> str:
    value = os.environ.get("PASI_PROJECTS_TOKEN", "").strip()
    if not value:
        raise RuntimeError("PASI_PROJECTS_TOKEN is required")
    return value


def graphql(query: str, variables: dict[str, object]) -> dict[str, object]:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        GRAPHQL_URL,
        method="POST",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token()}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "pasi-project5-roadmap-validator",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {detail}") from exc
    if body.get("errors"):
        raise RuntimeError(
            "GraphQL: " + "; ".join(str(e.get("message", e)) for e in body["errors"])
        )
    return body.get("data") or {}


def main() -> int:
    data = graphql(PROJECT_QUERY, {"login": OWNER, "number": PROJECT_NUMBER})
    project = ((data.get("user") or {}).get("projectV2"))
    if not project:
        raise RuntimeError(f"Project #{PROJECT_NUMBER} not found")

    if int(project.get("number", -1)) != PROJECT_NUMBER:
        raise RuntimeError("wrong Project number returned")

    views = project.get("views", {}).get("nodes") or []
    view = next(
        (v for v in views if int(v.get("number", -1)) == 1),
        next((v for v in views if v.get("name") == EXPECTED_VIEW_NAME), None),
    )
    if not view:
        raise RuntimeError("PASI Roadmap view #1 was not found")

    if view.get("name") != EXPECTED_VIEW_NAME:
        raise RuntimeError(
            f"expected roadmap view name {EXPECTED_VIEW_NAME!r}, got {view.get('name')!r}"
        )

    if view.get("layout") != "ROADMAP_LAYOUT":
        raise RuntimeError(
            f"Project #5 view #1 is not a roadmap; layout={view.get('layout')!r}"
        )

    fields = project.get("fields", {}).get("nodes") or []
    date_fields = {
        str(field.get("name")): str(field.get("dataType", "")).upper()
        for field in fields
        if field.get("__typename") == "ProjectV2Field"
    }

    for name in sorted(REQUIRED_DATE_FIELDS):
        if date_fields.get(name) != "DATE":
            raise RuntimeError(
                f"required roadmap field {name!r} must exist as DATE; "
                f"found {date_fields.get(name)!r}"
            )

    canonical_items = {}
    for item in project.get("items", {}).get("nodes") or []:
        content = item.get("content") or {}
        if (
            content.get("__typename") == "Issue"
            and content.get("repository", {}).get("nameWithOwner") == REPO
            and isinstance(content.get("number"), int)
        ):
            canonical_items[int(content["number"])] = item

    missing = [
        n for n in CANONICAL_NUMBERS
        if n not in canonical_items
    ]
    if missing:
        raise RuntimeError(f"canonical Project items missing: {missing}")

    missing_start = []
    missing_target = []
    for number, item in canonical_items.items():
        values = {}
        for node in (item.get("fieldValues", {}).get("nodes") or []):
            if node.get("__typename") == "ProjectV2ItemFieldDateValue":
                field_name = ((node.get("field") or {}).get("name"))
                if field_name in REQUIRED_DATE_FIELDS:
                    values[field_name] = node.get("date")

        if not values.get("Start date"):
            missing_start.append(number)
        if not values.get("Target date"):
            missing_target.append(number)

    if missing_start:
        raise RuntimeError(f"Start date is missing for canonical items: {missing_start}")
    if missing_target:
        raise RuntimeError(f"Target date is missing for canonical items: {missing_target}")

    print(f"Project #{PROJECT_NUMBER} roadmap contract: PASS")
    print(f"View: #{view['number']} {view['name']} ({view['layout']})")
    print("DATE fields: Start date [DATE], Target date [DATE]")
    print(f"Canonical items with both dates: {len(canonical_items)}")

    message = (
        "MANUAL VERIFICATION REQUIRED: GitHub's supported Projects API does not "
        "expose the Roadmap Date fields picker mapping, so CI cannot verify the "
        "timeline field selection. Open Project #5 in GitHub, open the PASI "
        "Roadmap view, choose Date fields, and verify Start date is mapped to "
        "the Start date field and Target date is mapped to the Target date field. "
        "This workflow intentionally fails until the mapping can be inspected "
        "by a supported API or an approved browser verification step."
    )
    print(f"::error title=Roadmap date-field mapping not API-verifiable::{message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

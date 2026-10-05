#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GRAPHQL_URL = "https://api.github.com/graphql"
API_VERSION = "2026-03-10"
OWNER = "riley-berg"
REPO = "riley-berg/PASI"
PROJECT_NUMBER = 5
ISSUES = [1, *range(19, 36)]
ROADMAP_LABEL = "roadmap"

ISSUE_META = re.compile(
    r"START_DATE:\s*(?P<start>\d{4}-\d{2}-\d{2}).*?"
    r"END_DATE:\s*(?P<end>\d{4}-\d{2}-\d{2}).*?"
    r"TEAM:\s*(?P<team>[^\n]+).*?"
    r"QUARTER:\s*(?P<quarter>[^\n]+).*?"
    r"OWNER:\s*(?P<owner>[^\n]+)",
    re.S,
)
ITERATION = re.compile(r"Iteration:\*\*\s*([^\n]+)")

SINGLE_OPTIONS = {
    "Team": [
        "Runtime", "Kernel", "Kernel Migration", "Capability Platform", "Trust",
        "Supervisor", "Agents", "Autonomous Development", "Engineering Data",
        "Verification", "Numerical", "Experiments", "Advanced AI", "Compute",
        "Workspace", "Collaboration", "Research", "Program",
    ],
    "Quarter": ["Q4-2026", "Q1-2027", "Q2-2027", "Q3-2027", "Q4-2027"],
}

DATE_RANGES: dict[int, tuple[str, str]] = {}


class Error(RuntimeError):
    pass


def token(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise Error(f"missing {name}")
    return value


def request_json(url: str, *, token_name: str, method: str = "GET", payload: Any = None) -> Any:
    token_value = token(token_name)
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token_value}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "pasi-project-metadata-reconciler",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise Error(f"GitHub API HTTP {exc.code}: {detail}") from exc


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        method="POST",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token('PASI_PROJECTS_TOKEN')}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "pasi-project-metadata-reconciler",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise Error(f"GraphQL HTTP {exc.code}: {detail}") from exc
    if body.get("errors"):
        raise Error("GraphQL: " + "; ".join(str(e.get("message", e)) for e in body["errors"]))
    return body.get("data") or {}


def issue(number: int) -> dict[str, Any]:
    return request_json(
        f"https://api.github.com/repos/{OWNER}/PASI/issues/{number}",
        token_name="GITHUB_TOKEN",
    )


def parse_metadata(number: int, body: str) -> dict[str, str]:
    match = ISSUE_META.search(body)
    iteration = ITERATION.search(body)
    if not match or not iteration:
        raise Error(f"issue #{number} is missing roadmap metadata")
    data = {k: v.strip() for k, v in match.groupdict().items()}
    data["iteration"] = iteration.group(1).strip()
    dt.date.fromisoformat(data["start"])
    dt.date.fromisoformat(data["end"])
    if data["end"] < data["start"]:
        raise Error(f"issue #{number} has end date before start date")
    return data


def issue_metadata() -> dict[int, dict[str, str]]:
    result = {}
    for number in ISSUES:
        item = issue(number)
        body = item.get("body") or ""
        if number == 1 and not ISSUE_META.search(body):
            meta = {
                "start": "2026-10-05",
                "end": "2027-11-14",
                "team": "Program",
                "quarter": "Q4-2026",
                "owner": OWNER,
                "iteration": "Program Roadmap",
            }
        else:
            meta = parse_metadata(number, body)
        if meta["owner"] != OWNER:
            raise Error(f"issue #{number} owner metadata is {meta['owner']!r}")
        result[number] = meta
        DATE_RANGES[number] = (meta["start"], meta["end"])
    return result


PROJECT_QUERY = """
query Project($login: String!, $number: Int!, $fieldAfter: String, $itemAfter: String) {
  user(login: $login) {
    projectV2(number: $number) {
      id
      number
      title
      url
      fields(first: 100, after: $fieldAfter) {
        pageInfo { hasNextPage endCursor }
        nodes {
          __typename
          ... on ProjectV2Field {
            id name dataType
            options(first: 100) {
              nodes { id name }
            }
          }
          ... on ProjectV2IterationField {
            id name
            configuration {
              iterations { id title startDate duration }
              completedIterations { id title startDate duration }
            }
          }
        }
      }
      items(first: 100, after: $itemAfter) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content {
            __typename
            ... on Issue {
              number
              repository { nameWithOwner }
              title
            }
          }
        }
      }
      views(first: 20) {
        nodes {
          id number name layout
          configuration {
            visibleFields(first: 100) {
              nodes {
                __typename
                ... on ProjectV2Field { id name dataType }
                ... on ProjectV2IterationField { id name }
              }
            }
          }
        }
      }
    }
  }
}
"""


def project() -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    views: list[dict[str, Any]] = []
    meta = None
    field_after = None
    item_after = None
    while meta is None or field_after is not None or item_after is not None:
        data = graphql(PROJECT_QUERY, {
            "login": OWNER,
            "number": PROJECT_NUMBER,
            "fieldAfter": field_after,
            "itemAfter": item_after,
        })
        p = (data.get("user") or {}).get("projectV2")
        if not p:
            raise Error(f"Project #{PROJECT_NUMBER} not found")
        if meta is None:
            meta = p
            views.extend((p.get("views") or {}).get("nodes") or [])
        fields.extend(x for x in ((p.get("fields") or {}).get("nodes") or []) if x)
        items.extend(x for x in ((p.get("items") or {}).get("nodes") or []) if x)
        fp = (p.get("fields") or {}).get("pageInfo") or {}
        ip = (p.get("items") or {}).get("pageInfo") or {}
        field_after = fp.get("endCursor") if fp.get("hasNextPage") else None
        item_after = ip.get("endCursor") if ip.get("hasNextPage") else None
    return {"meta": meta, "fields": fields, "items": items, "views": views}


def ensure_field(name: str, datatype: str, *, options: list[str] | None = None) -> dict[str, Any]:
    p = project()
    for field in p["fields"]:
        if str(field.get("name", "")).casefold() == name.casefold():
            if str(field.get("dataType", "")).upper() != datatype:
                raise Error(f"field {name!r} exists with wrong type")
            return field

    opts = [
        {"name": value, "color": "GRAY", "description": f"PASI {name} value {value}"}
        for value in (options or [])
    ]
    mutation = """
    mutation Create($projectId: ID!, $name: String!, $dataType: ProjectV2CustomFieldType!, $options: [ProjectV2SingleSelectFieldOptionInput!]) {
      createProjectV2Field(input: {
        projectId: $projectId
        name: $name
        dataType: $dataType
        singleSelectOptions: $options
      }) {
        projectV2Field {
          __typename
          ... on ProjectV2Field { id name dataType options(first:100) { nodes { id name } } }
          ... on ProjectV2IterationField { id name configuration { iterations { id title startDate duration } } }
        }
      }
    }
    """
    result = graphql(mutation, {
        "projectId": p["meta"]["id"],
        "name": name,
        "dataType": datatype.upper(),
        "options": opts or None,
    })
    created = ((result.get("createProjectV2Field") or {}).get("projectV2Field"))
    if not created:
        raise Error(f"failed to create field {name}")
    print(f"Created {datatype} field {name}")
    return created


def ensure_iteration_field(metadata: dict[int, dict[str, str]]) -> dict[str, Any]:
    p = project()
    for field in p["fields"]:
        if str(field.get("name", "")).casefold() == "iteration":
            if field.get("__typename") != "ProjectV2IterationField":
                raise Error("Iteration field exists with wrong type")
            field_id = field["id"]
            existing = [
                {"id": x["id"], "title": x["title"], "startDate": x["startDate"], "duration": x["duration"]}
                for x in ((field.get("configuration") or {}).get("iterations") or [])
            ]
            desired = []
            for number in ISSUES:
                m = metadata[number]
                start = dt.date.fromisoformat(m["start"])
                end = dt.date.fromisoformat(m["end"])
                duration = (end - start).days + 1
                desired.append({"title": m["iteration"], "startDate": m["start"], "duration": duration})
            current_by_title = {x["title"]: x for x in existing}
            iterations = []
            for wanted in desired:
                current = current_by_title.get(wanted["title"])
                value = dict(wanted)
                if current:
                    value["id"] = current["id"]
                iterations.append(value)
            update = """
            mutation Update($fieldId: ID!, $iterations: [ProjectV2Iteration!]!, $startDate: Date!, $duration: Int!) {
              updateProjectV2Field(input: {
                fieldId: $fieldId
                iterationConfiguration: {startDate: $startDate, duration: $duration, iterations: $iterations}
              }) {
                projectV2Field {
                  ... on ProjectV2IterationField {
                    id name configuration { iterations { id title startDate duration } }
                  }
                }
              }
            }
            """
            first = desired[0]
            data = graphql(update, {
                "fieldId": field_id,
                "iterations": iterations,
                "startDate": first["startDate"],
                "duration": first["duration"],
            })
            return ((data.get("updateProjectV2Field") or {}).get("projectV2Field")) or field

    p = project()
    desired = []
    for number in ISSUES:
        m = metadata[number]
        start = dt.date.fromisoformat(m["start"])
        end = dt.date.fromisoformat(m["end"])
        desired.append({
            "title": m["iteration"],
            "startDate": m["start"],
            "duration": (end - start).days + 1,
        })
    mutation = """
    mutation Create($projectId: ID!, $name: String!, $iterations: [ProjectV2Iteration!]!, $startDate: Date!, $duration: Int!) {
      createProjectV2Field(input: {
        projectId: $projectId
        name: $name
        dataType: ITERATION
        iterationConfiguration: {startDate: $startDate, duration: $duration, iterations: $iterations}
      }) {
        projectV2Field {
          ... on ProjectV2IterationField {
            id name configuration { iterations { id title startDate duration } }
          }
        }
      }
    }
    """
    first = desired[0]
    data = graphql(mutation, {
        "projectId": p["meta"]["id"],
        "name": "Iteration",
        "iterations": desired,
        "startDate": first["startDate"],
        "duration": first["duration"],
    })
    return ((data.get("createProjectV2Field") or {}).get("projectV2Field")) or {}


def field_option(field: dict[str, Any], name: str) -> str:
    for option in ((field.get("options") or {}).get("nodes") or []):
        if str(option.get("name", "")).casefold() == name.casefold():
            return str(option["id"])
    raise Error(f"option {name!r} missing from field {field.get('name')!r}")


SET_FIELD = """
mutation Set($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: $value
  }) {
    projectV2Item { id }
  }
}
"""


def set_project_value(project_id: str, item_id: str, field_id: str, value: dict[str, Any]) -> None:
    graphql(SET_FIELD, {
        "projectId": project_id,
        "itemId": item_id,
        "fieldId": field_id,
        "value": value,
    })


def ensure_roadmap_label() -> None:
    labels = request_json(
        f"https://api.github.com/repos/{OWNER}/PASI/labels?per_page=100",
        token_name="GITHUB_TOKEN",
    )
    names = {str(x.get("name", "")).casefold() for x in (labels or []) if isinstance(x, dict)}
    if ROADMAP_LABEL.casefold() in names:
        return
    request_json(
        f"https://api.github.com/repos/{OWNER}/PASI/labels",
        token_name="GITHUB_TOKEN",
        method="POST",
        payload={"name": ROADMAP_LABEL, "color": "ededed", "description": "Canonical PASI roadmap item"},
    )
    print("Created roadmap label")


def add_issue_metadata(metadata: dict[int, dict[str, str]]) -> None:
    ensure_roadmap_label()
    for number in ISSUES:
        # Every canonical roadmap issue is owned by Riley and carries the common roadmap label.
        request_json(
            f"https://api.github.com/repos/{OWNER}/PASI/issues/{number}",
            token_name="GITHUB_TOKEN",
            method="PATCH",
            payload={"assignees": [OWNER]},
        )
        request_json(
            f"https://api.github.com/repos/{OWNER}/PASI/issues/{number}/labels",
            token_name="GITHUB_TOKEN",
            method="POST",
            payload={"labels": [ROADMAP_LABEL]},
        )


def reconcile(metadata: dict[int, dict[str, str]]) -> None:
    p = project()
    allowed = {(REPO.casefold(), n) for n in ISSUES}
    existing = {}
    for item in p["items"]:
        content = item.get("content") or {}
        repo = str(((content.get("repository") or {}).get("nameWithOwner") or "")).casefold()
        number = content.get("number")
        if repo and isinstance(number, int):
            existing[(repo, number)] = item

    # Membership must be exactly the canonical 18 items.
    for key, item in list(existing.items()):
        if key not in allowed:
            delete = """
            mutation Delete($projectId: ID!, $itemId: ID!) {
              deleteProjectV2Item(input: {projectId: $projectId, itemId: $itemId}) {
                deletedItemId
              }
            }
            """
            graphql(delete, {"projectId": p["meta"]["id"], "itemId": item["id"]})
    p = project()
    existing = {}
    for item in p["items"]:
        content = item.get("content") or {}
        repo = str(((content.get("repository") or {}).get("nameWithOwner") or "")).casefold()
        number = content.get("number")
        if repo and isinstance(number, int):
            existing[(repo, number)] = item

    add = """
    mutation Add($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) { item { id } }
    }
    """
    issue_query = """
    query Issue($owner: String!, $repo: String!, $number: Int!) {
      repository(owner: $owner, name: $repo) { issue(number: $number) { id } }
    }
    """
    for number in ISSUES:
        key = (REPO.casefold(), number)
        if key in existing:
            continue
        data = graphql(issue_query, {"owner": OWNER, "repo": "PASI", "number": number})
        issue_id = (((data.get("repository") or {}).get("issue") or {}).get("id"))
        if not issue_id:
            raise Error(f"cannot resolve issue #{number}")
        graphql(add, {"projectId": p["meta"]["id"], "contentId": issue_id})

    # Canonical fields.
    p = project()
    fields_by = {str(f.get("name", "")).casefold(): f for f in p["fields"]}
    start_field = fields_by.get("start date") or ensure_field("Start date", "DATE")
    target_field = fields_by.get("target date") or ensure_field("Target date", "DATE")
    team_field = fields_by.get("team") or ensure_field("Team", "SINGLE_SELECT", options=SINGLE_OPTIONS["Team"])
    quarter_field = fields_by.get("quarter") or ensure_field("Quarter", "SINGLE_SELECT", options=SINGLE_OPTIONS["Quarter"])
    iteration_field = ensure_iteration_field(metadata)

    p = project()
    fields_by = {str(f.get("name", "")).casefold(): f for f in p["fields"]}
    start_field = fields_by["start date"]
    target_field = fields_by["target date"]
    team_field = fields_by["team"]
    quarter_field = fields_by["quarter"]
    iteration_field = fields_by["iteration"]

    iterations_by_title = {}
    for x in ((iteration_field.get("configuration") or {}).get("iterations") or []):
        iterations_by_title[str(x.get("title"))] = str(x["id"])

    # Refresh membership after adds.
    p = project()
    items_by_number = {}
    for item in p["items"]:
        content = item.get("content") or {}
        repo = str(((content.get("repository") or {}).get("nameWithOwner") or "")).casefold()
        n = content.get("number")
        if repo == REPO.casefold() and isinstance(n, int):
            items_by_number[n] = item

    for number in ISSUES:
        item = items_by_number[number]
        m = metadata[number]
        set_project_value(p["meta"]["id"], item["id"], start_field["id"], {"date": m["start"]})
        set_project_value(p["meta"]["id"], item["id"], target_field["id"], {"date": m["end"]})
        set_project_value(p["meta"]["id"], item["id"], team_field["id"], {"singleSelectOptionId": field_option(team_field, m["team"])})
        set_project_value(p["meta"]["id"], item["id"], quarter_field["id"], {"singleSelectOptionId": field_option(quarter_field, m["quarter"])})
        iteration_id = iterations_by_title.get(m["iteration"])
        if not iteration_id:
            raise Error(f"iteration {m['iteration']!r} missing")
        set_project_value(p["meta"]["id"], item["id"], iteration_field["id"], {"iterationId": iteration_id})

    add_issue_metadata(metadata)

    final = project()
    final_keys = sorted(
        (REPO.casefold(), int((item.get("content") or {}).get("number")))
        for item in final["items"]
        if ((item.get("content") or {}).get("repository") or {}).get("nameWithOwner") == REPO
        and isinstance((item.get("content") or {}).get("number"), int)
    )
    expected = sorted(allowed)
    if final_keys != expected:
        raise Error(f"Project membership mismatch: expected {len(expected)}, found {len(final_keys)}")

    print(f"Project #{PROJECT_NUMBER}: PASS")
    print("Items: 18")
    print("Assignee: riley-berg on all 18")
    print("Label: roadmap on all 18")
    print("Project fields: Start date, Target date, Team, Iteration, Quarter")
    print("Dates recomputed from current issue metadata.")
    print("Iterations recomputed from current Start/End metadata.")
    print("Roadmap date-field selection still requires the view's Date fields configuration if GitHub does not auto-select the fields.")


def main() -> int:
    try:
        metadata = issue_metadata()
        reconcile(metadata)
    except Exception as exc:
        print(f"PASI project metadata reconciliation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

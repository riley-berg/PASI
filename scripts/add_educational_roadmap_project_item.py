import datetime as dt
import json, os, urllib.request, urllib.error

API = "https://api.github.com/graphql"
OWNER, REPO, PROJECT = "riley-berg", "PASI", 6
ISSUE = int(os.environ.get("ROADMAP_ISSUE_NUMBER", "48"))
FALLBACK_WINDOW = ("2027-01-01", "2031-05-31", "q1-2027-q2-2031")

PARENT_MAP = {
 "all-paths":"All Paths", "computer-science":"Computer Science",
 "computer-engineering":"Computer Engineering", "electrical-engineering":"Electrical Engineering",
 "mechanical-engineering-robotics":"Mechanical Engineering / Robotics",
 "mechatronics-automation":"Mechatronics / Automation", "decision-gate":"Decision Gate"
}
CHILD_MAP = {
 "common-foundation":"Common Foundation", "credit-elimination-strategy":"Credit-Elimination Strategy",
 "engineering-academy-common-path":"Engineering Academy Common Path", "optimized-major-strategy":"Optimized Major Strategy",
 "ai-ml-automation-layer":"AI / ML / Automation Layer", "major-decision-etam-selection":"Major Decision / ETAM Selection",
 "pasi-interdisciplinary-engineering-portfolio":"PASI Interdisciplinary Engineering Portfolio"
}
GROUP_MAP = {
 "all-paths-shared-strategy":"All Paths — Shared Strategy",
 "computer-science-optimized":"Computer Science — Optimized Strategy",
 "computer-engineering-optimized":"Computer Engineering — Optimized Strategy",
 "electrical-engineering-optimized":"Electrical Engineering — Optimized Strategy",
 "me-robotics-optimized":"Mechanical Engineering / Robotics — Optimized Strategy",
 "mechatronics-automation-optimized":"Mechatronics / Automation — Optimized Strategy",
 "decision-gate-major-decision-etam-selection":"Decision Gate — Major Decision / ETAM Selection"
}

def gql(query, variables=None):
    token = os.environ.get("PASI_PROJECTS_TOKEN", "").strip()
    if not token: raise RuntimeError("PASI_PROJECTS_TOKEN is missing")
    req = urllib.request.Request(API, data=json.dumps({"query":query,"variables":variables or {}}).encode(), headers={
        "Authorization":"Bearer "+token, "Accept":"application/vnd.github+json",
        "X-GitHub-Api-Version":"2026-03-10", "Content-Type":"application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as res: data = json.load(res)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {e.code}: {e.read().decode('utf-8','replace')}") from e
    if data.get("errors"): raise RuntimeError("; ".join(x.get("message","GraphQL error") for x in data["errors"]))
    return data["data"]

PQ = """
query($login:String!,$n:Int!,$after:String){
 user(login:$login){projectV2(number:$n){
  id number title url
  fields(first:100){nodes{__typename ... on ProjectV2Field{id name dataType}
   ... on ProjectV2SingleSelectField{id name options{id name}}}}
  items(first:100,after:$after){pageInfo{hasNextPage endCursor}
   nodes{id content{__typename ... on Issue{number repository{nameWithOwner}}}}}
 }}
}
"""
IQ = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){id number title labels(first:100){nodes{name}}}}}"
ADD = "mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c}){item{id}}}"
SET = "mutation($p:ID!,$i:ID!,$f:ID!,$v:ProjectV2FieldValue!){updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:$v}){projectV2Item{id}}}"

def snap():
    after, meta, fields, items = None, None, [], []
    while True:
        data = gql(PQ, {"login":OWNER,"n":PROJECT,"after":after})
        p = (data.get("user") or {}).get("projectV2")
        if not p: raise RuntimeError(f"Project #{PROJECT} not found")
        if meta is None:
            meta = {k:p[k] for k in ("id","number","title","url")}
            fields = [f for f in p["fields"]["nodes"] if f]
        page = p["items"]
        items.extend(x for x in page["nodes"] if x)
        info = page["pageInfo"]
        if not info["hasNextPage"]: break
        after = info["endCursor"]
    return {**meta,"fields":fields,"items":items}

def norm(s): return " ".join(s.lower().replace("—","-").replace("–","-").split())

def label_value(labels, prefix):
    for label in labels:
        if label.startswith(prefix):
            return label[len(prefix):]
    return None

def set_value(project_id, item_id, field, value):
    gql(SET, {"p":project_id,"i":item_id,"f":field["id"],"v":value})
    print(f"SET FIELD: {field['name']} = {value}")

data = gql(IQ,{"o":OWNER,"r":REPO,"n":ISSUE})["repository"]["issue"]
if not data: raise RuntimeError(f"{OWNER}/{REPO}#{ISSUE} was not found")
labels = [x["name"] for x in data.get("labels",{}).get("nodes",[])]
if "roadmap" not in labels:
    print(f"SKIP: {OWNER}/{REPO}#{ISSUE} does not have the roadmap label")
    raise SystemExit(0)

parent_slug = label_value(labels, "roadmap-parent:")
child_slug = label_value(labels, "roadmap-child:")
group_slug = label_value(labels, "roadmap-group:")
start = label_value(labels, "roadmap-start:")
end = label_value(labels, "roadmap-end:")
quarter = label_value(labels, "roadmap-quarter:")
if ISSUE == 48 and not start and not end:
    start, end, quarter = FALLBACK_WINDOW

p = snap()
print(f"Project #{p['number']}: {p['title']} ({p['url']})")
for f in p["fields"]:
    print("FIELD",f.get("name"),f.get("dataType",f.get("__typename")),[o["name"] for o in f.get("options",[])])
item = next((x for x in p["items"] if (x.get("content") or {}).get("number")==ISSUE and ((x.get("content") or {}).get("repository") or {}).get("nameWithOwner","").lower()==f"{OWNER}/{REPO}".lower()),None)
if item:
    item_id = item["id"]
    print(f"ALREADY PRESENT: {OWNER}/{REPO}#{ISSUE}")
else:
    result = gql(ADD,{"p":p["id"],"c":data["id"]})["addProjectV2ItemById"]["item"]
    item_id = (result or {}).get("id")
    if not item_id: raise RuntimeError("Project add mutation returned no item ID")
    print(f"ADDED: {OWNER}/{REPO}#{ISSUE}")

for f in p["fields"]:
    name, typename = norm(f.get("name","")), f.get("__typename","")
    dtype = str(f.get("dataType","")).upper()
    if typename == "ProjectV2SingleSelectField":
        options = {norm(o["name"]):o for o in f.get("options",[])}
        wanted = None
        if name == "parent group" and parent_slug in PARENT_MAP: wanted = PARENT_MAP[parent_slug]
        elif name == "child group" and child_slug in CHILD_MAP: wanted = CHILD_MAP[child_slug]
        elif name == "group" and group_slug in GROUP_MAP: wanted = GROUP_MAP[group_slug]
        opt = options.get(norm(wanted)) if wanted else None
        if wanted and not opt: raise RuntimeError(f"Project option {wanted!r} missing from {f['name']}")
        if opt: set_value(p["id"],item_id,f,{"singleSelectOptionId":opt["id"]})
    elif name == "start date" and dtype == "DATE" and start:
        dt.date.fromisoformat(start); set_value(p["id"],item_id,f,{"date":start})
    elif name == "end date" and dtype == "DATE" and end:
        dt.date.fromisoformat(end); set_value(p["id"],item_id,f,{"date":end})
    elif name == "quarter" and dtype == "TEXT" and quarter:
        set_value(p["id"],item_id,f,{"text":quarter.upper()})
    elif name == "duration (days)" and dtype == "NUMBER" and start and end:
        days = (dt.date.fromisoformat(end)-dt.date.fromisoformat(start)).days+1
        set_value(p["id"],item_id,f,{"number":days})

# Verify by the item node itself, then verify project membership across every page.
node = gql("query($id:ID!){node(id:$id){__typename ... on ProjectV2Item{id}}}",{"id":item_id}).get("node") or {}
if node.get("id") != item_id: raise RuntimeError("Project item node verification failed")
final = snap()
verified = next((x for x in final["items"] if x.get("id")==item_id),None)
if not verified: raise RuntimeError("Project item ID was not present after paginated verification")
content = verified.get("content") or {}
if content.get("number") != ISSUE or ((content.get("repository") or {}).get("nameWithOwner","").lower() != f"{OWNER}/{REPO}".lower()):
    raise RuntimeError("Project item verification resolved to unexpected content")
print(f"VERIFY PASS: {OWNER}/{REPO}#{ISSUE} appears in {final['title']}")

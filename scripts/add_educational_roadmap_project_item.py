import datetime as dt
import json, os, urllib.request, urllib.error

API = "https://api.github.com/graphql"
OWNER, REPO, ISSUE, PROJECT = "riley-berg", "PASI", 48, 6
START_DATE, END_DATE = "2027-01-01", "2031-05-31"
QUARTER = "Q1-2027 - Q2-2031"

def gql(query, variables=None):
    token = os.environ.get("PASI_PROJECTS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("PASI_PROJECTS_TOKEN is missing")
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
IQ = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){id}}}"
ADD = "mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c}){item{id}}}"
SET = "mutation($p:ID!,$i:ID!,$f:ID!,$v:ProjectV2FieldValue!){updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:$v}){projectV2Item{id}}}"
NODE = "query($id:ID!){node(id:$id){__typename ... on ProjectV2Item{id}}}"

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

def norm(s):
    return " ".join(s.lower().replace("—","-").replace("–","-").split())

def set_value(project_id, item_id, field, value):
    gql(SET, {"p":project_id,"i":item_id,"f":field["id"],"v":value})
    print(f"SET FIELD: {field['name']} = {value}")

p = snap()
print(f"Project #{p['number']}: {p['title']} ({p['url']})")
for f in p["fields"]:
    print("FIELD",f.get("name"),f.get("dataType",f.get("__typename")),[o["name"] for o in f.get("options",[])])
issue_id = gql(IQ,{"o":OWNER,"r":REPO,"n":ISSUE})["repository"]["issue"]["id"]
item = next((x for x in p["items"] if (x.get("content") or {}).get("number")==ISSUE and ((x.get("content") or {}).get("repository") or {}).get("nameWithOwner","").lower()==f"{OWNER}/{REPO}".lower()),None)
if item:
    item_id = item["id"]
    print(f"ALREADY PRESENT: {REPO}#{ISSUE}")
else:
    result = gql(ADD,{"p":p["id"],"c":issue_id})["addProjectV2ItemById"]["item"]
    item_id = (result or {}).get("id")
    if not item_id: raise RuntimeError("Project add mutation returned no item ID")
    print(f"ADDED: {OWNER}/{REPO}#{ISSUE} as item {item_id}")

days = (dt.date.fromisoformat(END_DATE)-dt.date.fromisoformat(START_DATE)).days + 1
for f in p["fields"]:
    name = norm(f.get("name",""))
    typename = f.get("__typename","")
    datatype = str(f.get("dataType","")).upper()
    if typename == "ProjectV2SingleSelectField":
        options = {norm(o["name"]):o for o in f.get("options",[])}
        desired = None
        if name == "parent group": desired = "all paths"
        elif name == "child group": desired = "common foundation"
        elif name == "group": desired = "all paths - shared strategy"
        if desired:
            opt = options.get(desired)
            if not opt: raise RuntimeError(f"Required group option {desired!r} missing from {f['name']}")
            set_value(p["id"],item_id,f,{"singleSelectOptionId":opt["id"]})
    elif name == "start date" and datatype == "DATE":
        set_value(p["id"],item_id,f,{"date":START_DATE})
    elif name == "end date" and datatype == "DATE":
        set_value(p["id"],item_id,f,{"date":END_DATE})
    elif name == "quarter" and datatype == "TEXT":
        set_value(p["id"],item_id,f,{"text":QUARTER})
    elif name == "duration (days)" and datatype == "NUMBER":
        set_value(p["id"],item_id,f,{"number":days})

node = gql(NODE,{"id":item_id}).get("node") or {}
if node.get("id") != item_id:
    raise RuntimeError("Project item node verification failed")
final = snap()
verified = next((x for x in final["items"] if x.get("id")==item_id),None)
if not verified:
    raise RuntimeError("Project item ID was not present after paginated verification")
content = verified.get("content") or {}
if content.get("number") != ISSUE or ((content.get("repository") or {}).get("nameWithOwner","").lower() != f"{OWNER}/{REPO}".lower()):
    raise RuntimeError("Project item verification resolved to unexpected content")
print(f"VERIFY PASS: {OWNER}/{REPO}#{ISSUE} appears in {final['title']}")
print("Assigned: Parent Group=All Paths; Child Group=Common Foundation; Group=All Paths — Shared Strategy")
print(f"Planning-window dates: {START_DATE} to {END_DATE}; quarter range: {QUARTER}")

import json, os, urllib.request, urllib.error

API = "https://api.github.com/graphql"
OWNER, REPO, ISSUE, PROJECT = "riley-berg", "PASI", 48, 6

def gql(query, variables=None):
    token = os.environ.get("PASI_PROJECTS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("PASI_PROJECTS_TOKEN is missing")
    req = urllib.request.Request(API, data=json.dumps({"query": query, "variables": variables or {}}).encode(), headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10", "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as res: data = json.load(res)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {e.code}: {e.read().decode('utf-8','replace')}") from e
    if data.get("errors"): raise RuntimeError("; ".join(x.get("message","GraphQL error") for x in data["errors"]))
    return data["data"]

PQ = """
query($login:String!,$n:Int!){
 user(login:$login){ projectV2(number:$n){
  id number title url
  fields(first:100){nodes{
   __typename ... on ProjectV2Field{id name dataType}
   ... on ProjectV2SingleSelectField{id name options{id name}}
  }}
  items(first:100){nodes{id content{__typename ... on Issue{number repository{nameWithOwner}}}}}
 }}
}
"""
IQ = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){id}}}"
ADD = "mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c}){item{id}}}"
SET = "mutation($p:ID!,$i:ID!,$f:ID!,$v:ProjectV2FieldValue!){updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:$v}){projectV2Item{id}}}"

def snap():
    p = gql(PQ, {"login":OWNER,"n":PROJECT})["user"]["projectV2"]
    if not p: raise RuntimeError(f"Project #{PROJECT} not found")
    return p

def norm(s): return " ".join(s.lower().replace("—","-").replace("–","-").split())

p = snap()
print(f"Project #{p['number']}: {p['title']} {p['url']}")
fields = [f for f in p["fields"]["nodes"] if f]
for f in fields:
    print("FIELD", f.get("name"), f.get("dataType", f.get("__typename")), [o["name"] for o in f.get("options",[])])
iid = gql(IQ, {"o":OWNER,"r":REPO,"n":ISSUE})["repository"]["issue"]["id"]
items = p["items"]["nodes"]
item = next((x for x in items if (x.get("content") or {}).get("number")==ISSUE and ((x.get("content") or {}).get("repository") or {}).get("nameWithOwner","").lower()==f"{OWNER}/{REPO}".lower()), None)
if item:
    print(f"ALREADY PRESENT: {REPO}#{ISSUE}")
else:
    item = gql(ADD, {"p":p["id"],"c":iid})["addProjectV2ItemById"]["item"]
    print(f"ADDED: {REPO}#{ISSUE}")
p = snap()
item = next((x for x in p["items"]["nodes"] if (x.get("content") or {}).get("number")==ISSUE and ((x.get("content") or {}).get("repository") or {}).get("nameWithOwner","").lower()==f"{OWNER}/{REPO}".lower()), None)
if not item: raise RuntimeError("Issue did not appear in Project after add")
item_id = item["id"]
assigned = 0
for f in [x for x in p["fields"]["nodes"] if x]:
    name, typename = norm(f.get("name","")), f.get("__typename","")
    if typename != "ProjectV2SingleSelectField": continue
    options = {norm(o["name"]): o for o in f.get("options",[])}
    choices = []
    if name in ("parent","parent group","roadmap parent"): choices = ["all paths"]
    elif name in ("group","roadmap group"): choices = ["all paths - shared strategy","all paths"]
    elif name == "team": choices = ["program","all paths","shared strategy"]
    if choices:
        opt = next((options[c] for c in choices if c in options), None)
        if opt:
            gql(SET, {"p":p["id"],"i":item_id,"f":f["id"],"v":{"singleSelectOptionId":opt["id"]}})
            print(f"SET FIELD: {f['name']} = {opt['name']}")
            assigned += 1
        else:
            print(f"NO MATCH for field {f['name']}; options={list(options)}")
if assigned == 0:
    print("WARNING: no recognized Parent/Group/Team single-select field was assigned; inspect listed fields/options.")
p = snap()
ok = any((x.get("content") or {}).get("number")==ISSUE and ((x.get("content") or {}).get("repository") or {}).get("nameWithOwner","").lower()==f"{OWNER}/{REPO}".lower() for x in p["items"]["nodes"])
if not ok: raise RuntimeError("Final project membership verification failed")
print(f"VERIFY PASS: {REPO}#{ISSUE} is in {p['url']}")

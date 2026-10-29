# Neeto LinkedIn page (Neeto HQ Buffer account): keeps Buffer in line with plan.json. Run: python3 linkedin_sync.py
# One shared calendar for all five products. Rules set by Kirti on 27 Oct 2026:
# - One LinkedIn post per day, at POST_HOUR IST.
# - Backlog entries (group "backlog") go out on Mondays, Wednesdays and Thursdays, in list order (the list rotates
#   through the products, oldest first within each product).
# - Entries flagged "fast": true (new changelogs picked up by the daily run) take the earliest free Tuesday or Friday.
# - Entries whose date is past, or pinned, keep their dates. Entries with "held": true are never scheduled.
# - retired.json lists Buffer post ids to delete (posts pulled by a hold). Never prints the key.
import json, os, urllib.request, datetime as dt
KEY_ENV="BUFFER_KEY_NEETOHQ"   # set by the task from the Notion page "Neeto Products Buffer Key"
ORG="67c0674ae7cc23f4531d7043"; CH="6ae091ad2f1f5ed648e94da5"   # Neeto HQ organization, LinkedIn channel "neeto"
POST_HOUR=16             # IST
BACKLOG_DAYS=(0,2,3)     # Mon, Wed, Thu
FAST_DAYS=(1,4)          # Tue, Fri
KEY=os.environ.get(KEY_ENV,"").strip()
if not KEY: raise SystemExit("ERROR "+KEY_ENV+" is not set; the task must load it from the Notion page \"Neeto Products Buffer Key\"")
IST=dt.timezone(dt.timedelta(hours=5,minutes=30))
def gql(q,v=None):
    req=urllib.request.Request("https://api.buffer.com",data=json.dumps({"query":q,"variables":v or {}}).encode(),headers={"Authorization":"Bearer "+KEY,"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req,timeout=60))
CREATE="""mutation($input: CreatePostInput!){ createPost(input:$input){ ... on PostActionSuccess { post { id } } ... on MutationError { message } } }"""
EDIT="""mutation($input: EditPostInput!){ editPost(input:$input){ ... on PostActionSuccess { post { id } } ... on MutationError { message } } }"""
DELETE="""mutation($input: DeletePostInput!){ deletePost(input:$input){ __typename ... on MutationError { message } } }"""
def utc(day,hour): return dt.datetime(day.year,day.month,day.day,hour,0,tzinfo=IST).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def parse(s): return dt.datetime.strptime(s,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
def day_of(s): return parse(s).astimezone(IST).date()
def save(): json.dump(plan+held,open("plan.json","w"),indent=1,ensure_ascii=False)
plan=json.load(open("plan.json"))
now=dt.datetime.now(dt.timezone.utc); today=now.astimezone(IST).date(); hour_now=now.astimezone(IST).hour
# 0) delete retired posts
retired=json.load(open("retired.json")) if os.path.exists("retired.json") else []
still=[]
for pid in retired:
    r=(gql(DELETE,{"input":{"id":pid}}).get("data") or {}).get("deletePost") or {}
    if r.get("__typename")!="DeletePostSuccess" and "not found" not in json.dumps(r).lower(): still.append(pid); print("DELETE FAILED",pid,r.get("message"))
json.dump(still,open("retired.json","w"))
held=[e for e in plan if e.get("held")]
plan=[e for e in plan if not e.get("held")]
# 1) re-assign dates: one post per day
def fixed(e): return parse(e["li_at"])<=now or e.get("pinned")
taken={day_of(e["li_at"]) for e in plan if fixed(e) and e["li_at"][:4]!="2099"}
changed=0; edits=[]
def assign(e,at):
    global changed
    if e["li_at"]!=at:
        e["li_at"]=at; changed+=1
        if e.get("li_id"): edits.append(e)
def next_free(start,days):
    d=start
    while d.weekday() not in days or d in taken: d+=dt.timedelta(days=1)
    return d
start=today if hour_now<POST_HOUR-1 else today+dt.timedelta(days=1)
# fast entries first, so a new changelog never waits behind the backlog
for e in [e for e in plan if e.get("fast") and not fixed(e)]:
    d=next_free(start,FAST_DAYS); assign(e,utc(d,POST_HOUR)); taken.add(d)
d=start
for e in [e for e in plan if not e.get("fast") and not fixed(e)]:
    d=next_free(d,BACKLOG_DAYS); assign(e,utc(d,POST_HOUR)); taken.add(d); d+=dt.timedelta(days=1)
for e in edits:
    r=(gql(EDIT,{"input":{"id":e["li_id"],"text":e["linkedin"],"dueAt":e["li_at"],"mode":"customScheduled","schedulingType":"automatic"}}).get("data") or {}).get("editPost") or {}
    if not r.get("post"): print("EDIT FAILED",e["key"],r.get("message"))
plan.sort(key=lambda e:e["li_at"]); save()
print("dates re-assigned for",changed,"entries")
# 2) create missing posts, earliest first, until Buffer's per-channel limit
added=0
for e in plan:
    if e.get("li_id"): continue
    if parse(e["li_at"])<now: print("PAST DUE, skipped:",e["key"],e["li_at"]); continue
    inp={"text":e["linkedin"],"channelId":CH,"schedulingType":"automatic","mode":"customScheduled","dueAt":e["li_at"],"assets":[{"image":{"url":e["image"]}}]}
    r=(gql(CREATE,{"input":inp}).get("data") or {}).get("createPost") or {}
    if r.get("post"): e["li_id"]=r["post"]["id"]; added+=1; save()
    elif "limit" in r.get("message","").lower(): print("LIMIT reached - Buffer allows 10 scheduled posts per channel on the free plan; later posts wait for a free slot"); break
    else: print("ERROR",e["key"],r.get("message"))
print("added:",added,"| pending:",sum(1 for e in plan if not e.get("li_id")))
def fmt(s): return parse(s).astimezone(IST).strftime("%a %d %b %H:%M")
for e in plan: print(e["key"],e["product"][:11].ljust(11),fmt(e["li_at"]),"ok" if e.get("li_id") else "--","FAST" if e.get("fast") else "    ","|",e["title"][:50])
for e in held: print(e["key"],e["product"],"HELD |",e["title"][:50])

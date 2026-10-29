# NeetoDesk: keeps Buffer in line with plan2.json. Run: python3 buffer_sync.py
# Scheduling rules (shared Neeto LinkedIn page, so each product owns days/times):
# - Regular entries (backlog order) take PRIMARY days at PRIMARY_HOUR IST, in list order after the last fixed slot.
# - Entries flagged "fast": true (new changelogs picked up by the daily run) take the EARLIEST weekday on which this
#   product has no LinkedIn post yet: on a primary day at PRIMARY_HOUR, on any other weekday at OVERFLOW_HOUR, so two
#   products never post to LinkedIn at the same time. X goes out the next day on which this product has no X post, at X_HOUR.
# - Entries whose LinkedIn date is past, or pinned, keep their dates. Entries with "held": true are never scheduled.
# - retired.json lists Buffer post ids to delete (posts pulled by a hold). Never prints the key.
import json, os, urllib.request, datetime as dt
# The Neeto LinkedIn page lives in the NeetoCal Buffer account (NeetoCal key); X uses this product's own account.
KEY_ENV={"x":"BUFFER_KEY_NEETODESK","linkedin":"BUFFER_KEY_NEETOCAL"}   # keys come from environment variables set by the task (Notion page "Neeto Products Buffer Key")
CH={"x":"668fe98c602872be45f711a4","linkedin":"6ad61c61778b240e2f58b235"}
LI_HORIZON_DAYS=7        # LinkedIn queue (10 posts) is shared by all products: only create LinkedIn posts due within this many days
PRIMARY_DAYS=(1,3)       # 0=Mon ... 4=Fri. NeetoDesk: Tue/Thu (NeetoCal owns Mon/Wed)
PRIMARY_HOUR=18          # IST, LinkedIn on primary days
OVERFLOW_HOUR=13         # IST, LinkedIn on other weekdays (NeetoCal uses 11)
X_HOUR=18                # IST
KEYS={ch:os.environ.get(v,"").strip() for ch,v in KEY_ENV.items()}
for ch,v in KEY_ENV.items():
    if not KEYS[ch]: raise SystemExit("ERROR "+v+" is not set; the task must load it from the Notion page \"Neeto Products Buffer Key\"")
IST=dt.timezone(dt.timedelta(hours=5,minutes=30))
def gql(q,v=None,ch="x"):
    req=urllib.request.Request("https://api.buffer.com",data=json.dumps({"query":q,"variables":v or {}}).encode(),headers={"Authorization":"Bearer "+KEYS[ch],"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req,timeout=60))
CREATE="""mutation($input: CreatePostInput!){ createPost(input:$input){ ... on PostActionSuccess { post { id } } ... on MutationError { message } } }"""
EDIT="""mutation($input: EditPostInput!){ editPost(input:$input){ ... on PostActionSuccess { post { id } } ... on MutationError { message } } }"""
DELETE="""mutation($input: DeletePostInput!){ deletePost(input:$input){ __typename ... on MutationError { message } } }"""
def utc(day,hour): return dt.datetime(day.year,day.month,day.day,hour,0,tzinfo=IST).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def parse(s): return dt.datetime.strptime(s,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
def day_of(s): return parse(s).astimezone(IST).date()
def save(): json.dump(plan+held,open("plan2.json","w"),indent=1,ensure_ascii=False)
plan=json.load(open("plan2.json"))
now=dt.datetime.now(dt.timezone.utc); today=now.astimezone(IST).date(); hour_now=now.astimezone(IST).hour
# 0) delete retired posts
retired=json.load(open("retired.json")) if os.path.exists("retired.json") else []
still=[]
for item in retired:  # each item is [post_id, "x"|"linkedin"] (a bare id means "x")
    pid,ch=(item if isinstance(item,list) else [item,"x"])
    r=(gql(DELETE,{"input":{"id":pid}},ch).get("data") or {}).get("deletePost") or {}
    if r.get("__typename")!="DeletePostSuccess" and "not found" not in json.dumps(r).lower(): still.append(item); print("DELETE FAILED",pid,r.get("message"))
json.dump(still,open("retired.json","w"))
held=[e for e in plan if e.get("held")]
plan=[e for e in plan if not e.get("held")]
# guard (29 Oct 2026): before creating, reuse a post already scheduled in Buffer for the same changelog,
# so a plan file that failed to save can never cause duplicate posts.
import re as _re
POSTS_Q="""query($org:OrganizationId!,$ch:[ChannelId!],$after:String){ posts(input:{organizationId:$org, filter:{status:[scheduled], channelIds:$ch}}, first:100, after:$after){ edges{ node{ id text dueAt } } pageInfo{ hasNextPage endCursor } } }"""
def scheduled_posts(ch):
    try:
        org=gql("{ account { organizations { id } } }",None,ch)["data"]["account"]["organizations"][0]["id"]
        out,after=[],None
        while True:
            p=gql(POSTS_Q,{"org":org,"ch":[CH[ch]],"after":after},ch)["data"]["posts"]
            out+=[x["node"] for x in p["edges"]]
            if not p["pageInfo"]["hasNextPage"]: return out
            after=p["pageInfo"]["endCursor"]
    except Exception as ex:
        raise SystemExit("ERROR could not read scheduled posts from Buffer, so nothing was created: "+str(ex)[:150])
def find_existing(e,ch,posts,used):
    url=(e.get("url") or "").split("?")[0].rstrip("/")
    if "/changelogs/" not in url: return None
    pat=_re.compile(_re.escape("/changelogs/"+url.split("/")[-1])+r"(?=[?#/\s]|$)")
    for p in posts:
        if p["id"] not in used and pat.search(p.get("text") or ""): return p
    return None
EXISTING={"x":scheduled_posts("x")}; USED={e.get("x_id") for e in plan+held if e.get("x_id")}
# restore any post the plan lost (e.g. a save that failed) before dates are worked out, keeping its Buffer date
for e in plan:
    if e.get("x_id") or e.get("x_skip"): continue
    _ex=find_existing(e,"x",EXISTING["x"],USED)
    if _ex:
        e["x_id"]=_ex["id"]; e["x_at"]=_ex["dueAt"][:19]+"Z"; USED.add(_ex["id"]); print("ADOPTED existing Buffer post (no duplicate created):",e["key"],"x")
# 1) re-assign dates
def is_fixed(e): return parse(e["li_at"])<=now or e.get("pinned")
fixed=[e for e in plan if is_fixed(e)]
regular=[e for e in plan if not is_fixed(e) and not e.get("fast")]
fast=[e for e in plan if not is_fixed(e) and e.get("fast")]
regular.sort(key=lambda e:(0 if e.get("group")=="new" else 1, plan.index(e)))
fast.sort(key=lambda e: plan.index(e))
changed=0; edits=[]
def push_edit(e,ch,idk,atk):
    if ch=="linkedin": return   # LinkedIn posts live in the Neeto HQ account now
    if e.get(idk) and not e.get("li_skip" if ch=="linkedin" else "x_skip"): edits.append((e,ch,idk,atk))
def assign(e,li,x):
    global changed
    if e["li_at"]!=li or e["x_at"]!=x:
        e["li_at"],e["x_at"]=li,x; changed+=1
        push_edit(e,"x","x_id","x_at"); push_edit(e,"linkedin","li_id","li_at")
# regular entries: primary days in order after the last fixed/primary slot
last=max([day_of(e["li_at"]) for e in fixed], default=today)
d=last+dt.timedelta(days=1)
if d<=today and hour_now<PRIMARY_HOUR-1: d=today
elif d<=today: d=today+dt.timedelta(days=1)
for e in regular:
    while d.weekday() not in PRIMARY_DAYS: d+=dt.timedelta(days=1)
    assign(e,utc(d,PRIMARY_HOUR),utc(d+dt.timedelta(days=1),X_HOUR))
    d+=dt.timedelta(days=1)
# fast entries: earliest weekday with no LinkedIn post for this product
def taken(kind): return {day_of(e[kind]) for e in plan if kind in e and e[kind][:4]!="2099"}
for e in fast:
    e["li_at"]=e["x_at"]="2099-01-01T12:30:00Z"  # free its own slot before searching
    li_taken=taken("li_at"); x_taken=taken("x_at")
    d=today if hour_now<OVERFLOW_HOUR-1 else today+dt.timedelta(days=1)
    while d.weekday()>4 or d in li_taken: d+=dt.timedelta(days=1)
    li_hour=PRIMARY_HOUR if d.weekday() in PRIMARY_DAYS else OVERFLOW_HOUR
    if d==today and hour_now>=li_hour-1:
        d+=dt.timedelta(days=1)
        while d.weekday()>4 or d in li_taken: d+=dt.timedelta(days=1)
        li_hour=PRIMARY_HOUR if d.weekday() in PRIMARY_DAYS else OVERFLOW_HOUR
    xd=d+dt.timedelta(days=1)
    while xd in x_taken: xd+=dt.timedelta(days=1)
    assign(e,utc(d,li_hour),utc(xd,X_HOUR))
for e,ch,idk,atk in edits:
    r=(gql(EDIT,{"input":{"id":e[idk],"text":e[ch],"dueAt":e[atk],"mode":"customScheduled","schedulingType":"automatic"}},ch).get("data") or {}).get("editPost") or {}
    if not r.get("post"): print("EDIT FAILED",e["key"],ch,r.get("message"))
plan.sort(key=lambda e: e["li_at"]); save()
print("dates re-assigned for",changed,"entries")
# 2) create missing posts (earliest dates first)
added=0; limit={"x":False,"linkedin":False}
for e in plan:
    for ch,idk,atk in (("x","x_id","x_at"),):   # LinkedIn is handled by social-backfill/linkedin/linkedin_sync.py since 27 Oct 2026 (Neeto HQ account); this script does X only
        if e.get(idk) or limit[ch]: continue
        if e.get("li_skip" if ch=="linkedin" else "x_skip"): continue   # already published by hand; never create
        if parse(e[atk])<now: print("PAST DUE, skipped:",e["key"],ch,e[atk]); continue
        if ch=="linkedin" and parse(e[atk])>now+dt.timedelta(days=LI_HORIZON_DAYS): continue   # leave shared queue room for other products
        _ex=find_existing(e,ch,EXISTING[ch],USED)
        if _ex:
            e[idk]=_ex["id"]; USED.add(_ex["id"]); save(); print("ADOPTED existing Buffer post (no duplicate created):",e["key"],ch)
            if (_ex.get("dueAt") or "")[:19]!=e[atk][:19]:
                r=(gql(EDIT,{"input":{"id":_ex["id"],"text":e[ch],"dueAt":e[atk],"mode":"customScheduled","schedulingType":"automatic"}},ch).get("data") or {}).get("editPost") or {}
                if not r.get("post"): print("EDIT FAILED",e["key"],ch,r.get("message"))
            continue
        inp={"text":e[ch],"channelId":CH[ch],"schedulingType":"automatic","mode":"customScheduled","dueAt":e[atk],"assets":[{"image":{"url":e["image"]}}]}
        r=(gql(CREATE,{"input":inp},ch).get("data") or {}).get("createPost") or {}
        if r.get("post"): e[idk]=r["post"]["id"]; added+=1; save()
        elif "limit" in r.get("message","").lower(): limit[ch]=True
        else: print("ERROR",e["key"],ch,r.get("message"))
print("added:",added,"| pending:",sum(1 for e in plan for k,sk in (("x_id","x_skip"),("li_id","li_skip")) if not e.get(k) and not e.get(sk)))
def fmt(s): return parse(s).astimezone(IST).strftime("%a %d %b %H:%M")
for e in plan: print(e["key"],"LI",fmt(e["li_at"]),"skip" if e.get("li_skip") else ("ok" if e.get("li_id") else "--"),"X",fmt(e["x_at"]),"ok" if e.get("x_id") else "--","FAST" if e.get("fast") else "","|",e["title"][:45])
for e in held: print(e["key"],"HELD |",e["title"][:45])

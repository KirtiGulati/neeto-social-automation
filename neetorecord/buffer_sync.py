# NeetoRecord: keeps Buffer in line with plan2.json. Run: python3 buffer_sync.py
# Scheduling rules (the Neeto LinkedIn page is shared by all products, so each product owns a day and an hour):
# - Regular (backlog) entries: LinkedIn on this product's PRIMARY_DAY at PRIMARY_HOUR IST, one per week, in list order.
#   X goes out on Tuesdays and Thursdays at X_HOUR IST, in list order, independent of the LinkedIn date.
# - Entries flagged "fast": true (new changelogs picked up by the daily run) take the EARLIEST weekday on which this
#   product has no LinkedIn post yet, at FAST_HOUR (PRIMARY_HOUR on the primary day); X takes the earliest weekday on
#   which this product has no X post, at X_HOUR. Other products use other hours, so LinkedIn posts never clash.
# - Entries whose LinkedIn date is past, or pinned, keep their dates. Entries with "held": true are never scheduled.
# - retired.json lists Buffer post ids to delete (posts pulled by a hold). Never prints a key.
import json, os, urllib.request, datetime as dt
# The Neeto LinkedIn page lives in the NeetoCal Buffer account (NeetoCal key); X uses this product's own account.
KEY_ENV={"x":"BUFFER_KEY_NEETORECORD","linkedin":"BUFFER_KEY_NEETOCAL"}   # keys come from environment variables set by the task (Notion page "Neeto Products Buffer Key")
CH={"x":None,"linkedin":"6ad61c61778b240e2f58b235"}   # x is filled from channels.json (discovered on first run)
LI_HORIZON_DAYS=7        # LinkedIn queue (10 posts) is shared by all products: only create LinkedIn posts due within this many days
PRIMARY_DAY=0          # 0=Mon ... 4=Fri
PRIMARY_HOUR=15        # IST, LinkedIn on the primary day
FAST_HOUR=15              # IST, LinkedIn for fast entries on other weekdays (NeetoCal 11am, NeetoDesk 1pm, NeetoRecord 3pm, NeetoForm 4pm, NeetoChat 5pm)
X_DAYS=(1,3)             # Tue/Thu for backlog X posts
X_HOUR=18                # IST
KEYS={ch:os.environ.get(v,"").strip() for ch,v in KEY_ENV.items()}
for ch,v in KEY_ENV.items():
    if not KEYS[ch]: raise SystemExit("ERROR "+v+" is not set; the task must load it from the Notion page \"Neeto Products Buffer Key\"")
IST=dt.timezone(dt.timedelta(hours=5,minutes=30))
def gql(q,v=None,ch="x"):
    req=urllib.request.Request("https://api.buffer.com",data=json.dumps({"query":q,"variables":v or {}}).encode(),headers={"Authorization":"Bearer "+KEYS[ch],"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req,timeout=60))
# discover this product's X channel once and remember it
if os.path.exists("channels.json"): CH["x"]=json.load(open("channels.json"))["x"]
else:
    r=gql('{ account { organizations { id name channels { id name service } } } }')
    if not r.get("data"): raise SystemExit("ERROR could not list channels (does the key have account:read?): "+json.dumps(r.get("errors"))[:200])
    xs=[(o["id"],c) for o in r["data"]["account"]["organizations"] for c in o["channels"] if c["service"] in ("twitter","x")]
    if len(xs)!=1: raise SystemExit("ERROR expected exactly one X channel, found: "+json.dumps(xs))
    CH["x"]=xs[0][1]["id"]; json.dump({"x":CH["x"],"x_name":xs[0][1]["name"],"organization":xs[0][0]},open("channels.json","w"),indent=1)
    print("X channel discovered:",xs[0][1]["name"],CH["x"])
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
for item in retired:  # each item is [post_id, "x"|"linkedin"]
    pid,ch=(item if isinstance(item,list) else [item,"x"])
    r=(gql(DELETE,{"input":{"id":pid}},ch).get("data") or {}).get("deletePost") or {}
    if r.get("__typename")!="DeletePostSuccess" and "not found" not in json.dumps(r).lower(): still.append(item); print("DELETE FAILED",pid,r.get("message"))
json.dump(still,open("retired.json","w"))
held=[e for e in plan if e.get("held")]
plan=[e for e in plan if not e.get("held")]
# 1) re-assign dates
def li_fixed(e): return parse(e["li_at"])<=now or e.get("pinned")
def x_fixed(e): return parse(e["x_at"])<=now or e.get("pinned") or (e.get("x_id") and day_of(e["x_at"])==today)   # a post already in Buffer for later today stays put
regular=[e for e in plan if not e.get("fast")]
fast=[e for e in plan if e.get("fast")]
regular.sort(key=lambda e:(0 if e.get("group")=="new" else 1, plan.index(e)))
fast.sort(key=lambda e: plan.index(e))
changed=0; edits=[]
def push_edit(e,ch,idk,atk):
    if ch=="linkedin": return   # LinkedIn posts live in the Neeto HQ account now
    if e.get(idk) and not e.get("li_skip" if ch=="linkedin" else "x_skip"): edits.append((e,ch,idk,atk))
def set_li(e,li):
    global changed
    if e["li_at"]!=li: e["li_at"]=li; changed+=1; push_edit(e,"linkedin","li_id","li_at")
def set_x(e,x):
    global changed
    if e["x_at"]!=x: e["x_at"]=x; changed+=1; push_edit(e,"x","x_id","x_at")
def next_free(start,days_allowed,taken):
    d=start
    while d.weekday() not in days_allowed or d in taken: d+=dt.timedelta(days=1)
    return d
# regular LinkedIn: one per week on PRIMARY_DAY, in order, after the last fixed slot
li_taken={day_of(e["li_at"]) for e in plan if li_fixed(e) and e["li_at"][:4]!="2099"}
d=today+dt.timedelta(days=1) if hour_now>=PRIMARY_HOUR-1 else today
for e in regular:
    if li_fixed(e): continue
    d=next_free(d,{PRIMARY_DAY},li_taken); set_li(e,utc(d,PRIMARY_HOUR)); li_taken.add(d); d+=dt.timedelta(days=1)
# regular X: Tue/Thu in order, after the last fixed slot (never today, so the approval thread has a day's notice)
x_taken={day_of(e["x_at"]) for e in plan if x_fixed(e) and e["x_at"][:4]!="2099"}
d=today+dt.timedelta(days=1)
for e in regular:
    if x_fixed(e): continue
    d=next_free(d,X_DAYS,x_taken); set_x(e,utc(d,X_HOUR)); x_taken.add(d); d+=dt.timedelta(days=1)
# fast entries: earliest weekday with no LinkedIn post, and earliest weekday with no X post
for e in fast:
    if not li_fixed(e):
        start=today if hour_now<min(FAST_HOUR,PRIMARY_HOUR)-1 else today+dt.timedelta(days=1)
        d=next_free(start,{0,1,2,3,4},li_taken); h=PRIMARY_HOUR if d.weekday()==PRIMARY_DAY else FAST_HOUR
        if d==today and hour_now>=h-1: d=next_free(d+dt.timedelta(days=1),{0,1,2,3,4},li_taken); h=PRIMARY_HOUR if d.weekday()==PRIMARY_DAY else FAST_HOUR
        set_li(e,utc(d,h)); li_taken.add(d)
    if not x_fixed(e):
        start=today if hour_now<X_HOUR-1 else today+dt.timedelta(days=1)
        d=next_free(start,{0,1,2,3,4},x_taken); set_x(e,utc(d,X_HOUR)); x_taken.add(d)
for e,ch,idk,atk in edits:
    r=(gql(EDIT,{"input":{"id":e[idk],"text":e[ch],"dueAt":e[atk],"mode":"customScheduled","schedulingType":"automatic"}},ch).get("data") or {}).get("editPost") or {}
    if not r.get("post"): print("EDIT FAILED",e["key"],ch,r.get("message"))
plan.sort(key=lambda e: e["li_at"]); save()
print("dates re-assigned for",changed,"entries")
# 2) create missing posts (earliest dates first)
added=0; limit={"x":False,"linkedin":False}
for ch,idk,atk in (("x","x_id","x_at"),):   # LinkedIn is handled by social-backfill/linkedin/linkedin_sync.py since 27 Oct 2026 (Neeto HQ account); this script does X only
    for e in sorted(plan,key=lambda e:e[atk]):
        if e.get(idk) or limit[ch]: continue
        if e.get("li_skip" if ch=="linkedin" else "x_skip"): continue   # already published by hand; never create
        if parse(e[atk])<now: print("PAST DUE, skipped:",e["key"],ch,e[atk]); continue
        if ch=="linkedin" and parse(e[atk])>now+dt.timedelta(days=LI_HORIZON_DAYS): continue   # leave shared queue room for other products
        inp={"text":e[ch],"channelId":CH[ch],"schedulingType":"automatic","mode":"customScheduled","dueAt":e[atk],"assets":[{"image":{"url":e["image"]}}]}
        r=(gql(CREATE,{"input":inp},ch).get("data") or {}).get("createPost") or {}
        if r.get("post"): e[idk]=r["post"]["id"]; added+=1; save()
        elif "limit" in r.get("message","").lower(): limit[ch]=True; print("LIMIT reached for",ch,"- Buffer allows 10 scheduled posts per channel; later posts wait for a free slot")
        else: print("ERROR",e["key"],ch,r.get("message"))
print("added:",added,"| pending:",sum(1 for e in plan for k,sk in (("x_id","x_skip"),("li_id","li_skip")) if not e.get(k) and not e.get(sk)))
def fmt(s): return parse(s).astimezone(IST).strftime("%a %d %b %H:%M")
for e in plan: print(e["key"],"LI",fmt(e["li_at"]),"skip" if e.get("li_skip") else ("ok" if e.get("li_id") else "--"),"X",fmt(e["x_at"]),"ok" if e.get("x_id") else "--","FAST" if e.get("fast") else "","|",e["title"][:45])
for e in held: print(e["key"],"HELD |",e["title"][:45])

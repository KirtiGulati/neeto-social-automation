#!/usr/bin/env python3
"""Stateless Buffer sync for Neeto changelog posts (X per product, LinkedIn shared).

Buffer and the Slack approval threads are the only state. Nothing is written back anywhere.

  python3 sync.py x neetocal entries.json      # X posts for one product, from that product's Buffer account
  python3 sync.py linkedin entries.json        # LinkedIn posts for all products, from the Neeto HQ Buffer account

entries.json: a list written by the scheduled task from the "New · " threads it read in Slack, one object per thread:
  {"url": plain changelog URL, "title": "...", "image": cover image URL, "text": the X (or LinkedIn) post text,
   "created": the thread parent's message_ts, "held": true/false}
Entries also come from seed.json (older entries whose drafts are not in Slack replies); an entries.json entry with the
same URL overrides the seed. Entries are matched to Buffer posts by changelog slug or by the start of the post text.

What it does, in order: deletes the scheduled post of every held entry; reports entries already sent; moves any
scheduled post that sits on a weekend to the next free weekday; edits a scheduled post whose text changed; then
creates the missing posts, newest changelogs first, into free slots until Buffer's 10-per-channel limit.
Never prints a key. Exit code is 0 unless Buffer could not be read.
"""
import json, os, re, sys, difflib, datetime as dt, urllib.request

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NEW_FROM = dt.date(2026, 10, 27)          # threads created on or after this date are "new", earlier ones "backlog"
HERE = os.path.dirname(os.path.abspath(__file__))

PRODUCTS = {   # X: one post per product per weekday at 6:00pm IST, from the product's own Buffer account
    "neetocal":    {"env": "BUFFER_KEY_NEETOCAL",    "org": "668fe2a9c6b584323b2efb89", "ch": "668fe37a602872be45849926"},
    "neetodesk":   {"env": "BUFFER_KEY_NEETODESK",   "org": "668fe949942a79ccd26c4ef3", "ch": "668fe98c602872be45f711a4"},
    "neetorecord": {"env": "BUFFER_KEY_NEETORECORD", "org": "6690cf8eb6e1940e58c17cd4", "ch": "6690d295602872be4535f020"},
    "neetoform":   {"env": "BUFFER_KEY_NEETOFORM",   "org": "668efa3d4181b7d4b3a0a3f9", "ch": "677e82104697c1deff14a0fb"},
    "neetochat":   {"env": "BUFFER_KEY_NEETOCHAT",   "org": "668fe682b65d517debc548a2", "ch": "668fe882602872be45e31d65"},
}
LINKEDIN = {"env": "BUFFER_KEY_NEETOHQ", "org": "67c0674ae7cc23f4531d7043", "ch": "6ae091ad2f1f5ed648e94da5"}
X_HOUR, LI_HOUR = 18, 16
LI_BACKLOG_DAYS, LI_NEW_DAYS, WEEKDAYS = (0, 2, 3), (1, 4), (0, 1, 2, 3, 4)

mode = sys.argv[1] if len(sys.argv) > 1 else ""
if mode == "x":
    product, entries_file = sys.argv[2], sys.argv[3]
    cfg, hour = PRODUCTS[product], X_HOUR
elif mode == "linkedin":
    product, entries_file = "linkedin", sys.argv[2]
    cfg, hour = LINKEDIN, LI_HOUR
else:
    raise SystemExit("usage: sync.py x <product> entries.json | sync.py linkedin entries.json")
KEY = os.environ.get(cfg["env"], "").strip()
if not KEY:
    raise SystemExit("ERROR " + cfg["env"] + " is not set; load it from the Notion page \"Neeto Products Buffer Key\"")

DRY = os.environ.get("DRY") == "1"     # DRY=1: read Buffer but change nothing
def gql(q, v=None):
    if DRY and q.lstrip().startswith("mutation"):
        return {"data": {"createPost": {"__typename": "PostActionSuccess", "post": {"id": "dry", "dueAt": (v or {}).get("in", {}).get("dueAt")}},
                         "editPost": {"__typename": "PostActionSuccess", "post": {"id": "dry"}},
                         "deletePost": {"__typename": "DeletePostSuccess"}}}
    req = urllib.request.Request("https://api.buffer.com", data=json.dumps({"query": q, "variables": v or {}}).encode(),
                                 headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))

POSTS_Q = """query($org:OrganizationId!,$ch:[ChannelId!],$st:[PostStatus!]!,$after:String){
  posts(input:{organizationId:$org, filter:{status:$st, channelIds:$ch}}, first:100, after:$after){
    edges{ node{ id text dueAt channelId status } } pageInfo{ hasNextPage endCursor } } }"""
CREATE = """mutation($in: CreatePostInput!){ createPost(input:$in){ __typename ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""
EDIT = """mutation($in: EditPostInput!){ editPost(input:$in){ __typename ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""
DELETE = """mutation($in: DeletePostInput!){ deletePost(input:$in){ __typename ... on MutationError { message } } }"""

def utc(day, h): return dt.datetime(day.year, day.month, day.day, h, 0, tzinfo=IST).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def parse(s): return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
def day_of(s): return parse(s).astimezone(IST).date()
def fmt(s): return parse(s).astimezone(IST).strftime("%a %d %b %H:%M")
def slug(url): return (url or "").split("?")[0].rstrip("/").split("/")[-1]
def norm(t): return re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", t or "")).strip().lower()
def created_date(e):
    try: return dt.datetime.fromtimestamp(float(e.get("created") or 0), IST).date()
    except Exception: return dt.date(2000, 1, 1)

# ---- entries: seed first, then the task's entries.json (same URL overrides the seed) ----
seed = json.load(open(os.path.join(HERE, "seed.json")))
entries = {}
for e in seed:
    if (e["kind"] == "x" and e["product"] == product) or (e["kind"] == "linkedin" and mode == "linkedin"):
        entries[slug(e["url"])] = dict(e)
for e in json.load(open(entries_file)):
    s = slug(e["url"]); old = entries.get(s, {})
    merged = dict(old); merged.update({k: v for k, v in e.items() if v not in (None, "")})
    merged.setdefault("group", "new" if created_date(merged) >= NEW_FROM else "backlog")
    entries[s] = merged
for s, e in entries.items():
    e.setdefault("group", "new"); e.setdefault("held", False); e["slug"] = s
    e.setdefault("order", str(e.get("created") or ""))

# ---- Buffer state ----
org, ch = cfg["org"], cfg["ch"]
def posts(status):
    out, after = [], None
    while True:
        r = gql(POSTS_Q, {"org": org, "ch": [ch], "st": [status], "after": after})
        if "errors" in r: raise SystemExit("ERROR could not read Buffer posts: " + json.dumps(r["errors"])[:200])
        p = r["data"]["posts"]; out += [x["node"] for x in p["edges"]]
        if not p["pageInfo"]["hasNextPage"]: return out
        after = p["pageInfo"]["endCursor"]
scheduled, sent = posts("scheduled"), posts("sent")
sent.sort(key=lambda p: p["dueAt"] or "", reverse=True)

def match(e, pool):
    """A Buffer post is this entry's post if it carries the changelog slug, or its text is the entry's text (allowing
    for small edits and Buffer's link shortening, which hides the slug in sent LinkedIn posts)."""
    pat = re.compile(re.escape("/changelogs/" + e["slug"]) + r"(?=[?#/\s]|$)")
    mine = norm(e.get("text", ""))
    for p in pool:
        t = p.get("text") or ""
        if pat.search(t): return p
        if mine and (norm(t).startswith(mine[:60]) or difflib.SequenceMatcher(None, mine, norm(t)).ratio() >= 0.8): return p
    return None

taken = {day_of(p["dueAt"]) for p in scheduled}
now = dt.datetime.now(dt.timezone.utc); today = now.astimezone(IST).date(); hour_now = now.astimezone(IST).hour
start = today if hour_now < hour - 1 else today + dt.timedelta(days=1)
def next_free(d, days):
    while d.weekday() not in days or d in taken: d += dt.timedelta(days=1)
    return d
def edit(p, text, image, due):
    r = (gql(EDIT, {"in": {"id": p["id"], "text": text, "dueAt": due, "mode": "customScheduled", "schedulingType": "automatic",
                           "assets": [{"image": {"url": image}}] if image else []}}).get("data") or {}).get("editPost") or {}
    return r.get("post")

report, pending = [], []
for e in sorted(entries.values(), key=lambda e: (e["group"] != "new", e["order"])):
    sp, pp = match(e, scheduled), match(e, sent)
    if e["held"]:
        if sp:
            r = (gql(DELETE, {"in": {"id": sp["id"]}}).get("data") or {}).get("deletePost") or {}
            if r.get("__typename", "").endswith("Success") or "not found" in json.dumps(r).lower():
                scheduled.remove(sp); taken.discard(day_of(sp["dueAt"])); report.append(("PULLED", e, None))
            else: report.append(("DELETE FAILED", e, r.get("message")))
        else: report.append(("HELD", e, None))
        continue
    if pp and not sp:
        report.append(("POSTED", e, pp["dueAt"])); continue
    if sp:
        due = sp["dueAt"]; d = day_of(due)
        if d.weekday() > 4 or (d < start and d > today):     # weekend post (or a stale one): move it
            taken.discard(d); nd = next_free(max(start, d), WEEKDAYS); due = utc(nd, hour)
            if edit(sp, e.get("text") or sp["text"], e.get("image"), due):
                taken.add(nd); sp["dueAt"] = due; report.append(("MOVED", e, due))
            else: taken.add(d); report.append(("EDIT FAILED", e, "could not move off " + str(d)))
        elif e.get("text") and norm(e["text"]) != norm(sp["text"]):
            if edit(sp, e["text"], e.get("image"), due): sp["text"] = e["text"]; report.append(("UPDATED", e, due))
            else: report.append(("EDIT FAILED", e, "text update"))
        else: report.append(("SCHEDULED", e, due))
        continue
    if not e.get("text"): report.append(("NO TEXT", e, None)); continue
    pending.append(e)

# ---- create missing posts: new entries first, then the backlog, each into the earliest free slot ----
backlog_left = any(e["group"] == "backlog" for e in pending)
limit_hit = False
for e in pending:
    if limit_hit: report.append(("PENDING (limit)", e, None)); continue
    if mode == "linkedin":
        days = LI_BACKLOG_DAYS if e["group"] == "backlog" else (LI_NEW_DAYS if backlog_left else WEEKDAYS)
    else: days = WEEKDAYS
    d = next_free(start, days); due = utc(d, hour)
    inp = {"channelId": ch, "text": e["text"], "dueAt": due, "mode": "customScheduled", "schedulingType": "automatic",
           "needsApproval": False, "assets": [{"image": {"url": e["image"]}}] if e.get("image") else [], "source": "neeto-social-sync"}
    r = (gql(CREATE, {"in": inp}).get("data") or {}).get("createPost") or {}
    if r.get("post"): taken.add(d); report.append(("CREATED", e, due))
    elif r.get("__typename") == "LimitReachedError" or "limit" in (r.get("message") or "").lower():
        limit_hit = True; report.append(("PENDING (limit)", e, None))
    else: report.append(("ERROR", e, r.get("message") or json.dumps(r)[:120]))

def sort_key(x): return (x[2] or "9999") if x[0] in ("CREATED", "SCHEDULED", "MOVED", "UPDATED") else "9999" + x[0]
for st, e, extra in sorted(report, key=sort_key):
    when = fmt(extra) if extra and extra[:2] == "20" else (extra or "")
    print(f"{st:16} {e['slug']:55} {when}  | {e.get('title','')[:50]}")
if limit_hit: print("LIMIT reached: Buffer allows 10 scheduled posts per channel; the rest are created on later runs")

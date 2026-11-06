# v2: stateless sync (since 6 Nov 2026)

Why: the cloud sessions that run the scheduled tasks can only reach GitHub repositories that are attached to the
session, and a scheduled run cannot attach one. From 3 Nov 2026 every X and LinkedIn run hung at `git clone` and
nothing new reached Buffer. A public repository can be cloned by any session, but not pushed to, so the tasks now
write nothing back: Buffer and the Slack approval threads are the only state.

## Files
- `sync.py`: one script for both jobs.
  - `python3 sync.py x <product> entries.json` schedules X posts for one product from its own Buffer account.
  - `python3 sync.py linkedin entries.json` schedules LinkedIn posts for all products from the Neeto HQ account.
- `seed.json`: the entries that were in the old plan files on 6 Nov 2026 (texts, images, backlog order). Needed
  because the drafts of the oldest entries are not in Slack replies. New entries come from Slack.

## What a run does
1. The task reads every "New · " thread in the approval channels and writes `entries.json`
   (url, title, image, text, created = parent message_ts, held).
2. `sync.py` reads Buffer's scheduled and sent posts and matches each entry by changelog slug, or by its text when
   Buffer has shortened the link.
3. Held entries: the scheduled post is deleted. Already sent: reported, nothing done.
4. Scheduled posts on a weekend are moved to the next free weekday. A changed draft updates the Buffer post.
5. Missing posts are created, newest changelogs first, then the backlog, into the earliest free slot, until Buffer's
   10-per-channel limit. The rest are created on later runs as slots free up.

## Rules
- X: one post per product per weekday at 6:00pm IST, never on a weekend.
- LinkedIn: one post per day at 4:00pm IST. Backlog entries go out Monday, Wednesday and Thursday in the order set
  on 27 Oct 2026; new changelogs take the earliest free Tuesday or Friday. Once no backlog entry is waiting, new
  changelogs may use any free weekday.
- Set `DRY=1` to read Buffer and print what would change without changing anything.
- Keys come from the environment (`BUFFER_KEY_NEETOHQ`, `BUFFER_KEY_NEETOCAL`, ...), loaded by the task from the
  Notion page "Neeto Products Buffer Key". No key is ever stored or printed.

The old per-product scripts and plan files in the other folders are kept for reference only.

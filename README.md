# Neeto social automation

Scripts and plan files for the changelog-to-social workflow (X and LinkedIn scheduling in Buffer) for NeetoCal, NeetoDesk, NeetoRecord, NeetoForm and NeetoChat.

No keys or tokens are stored here. The scheduled tasks read Buffer keys from the Notion page "Neeto Products Buffer Key".

## Layout
- `neetocal/`, `neetodesk/`, `neetorecord/`, `neetoform/`, `neetochat/`: X scheduling for each product (`buffer_sync.py`, `plan2.json`, `retired.json`, and `channels.json` where used).
- `linkedin/`: the shared Neeto LinkedIn calendar in the Neeto HQ Buffer account (`linkedin_sync.py`, `plan.json`, `retired.json`).

## How the scheduled tasks use this
Each run downloads this repository, reads the Buffer keys from Notion into environment variables (`BUFFER_KEY_NEETOHQ`, `BUFFER_KEY_NEETOCAL`, `BUFFER_KEY_NEETODESK`, `BUFFER_KEY_NEETORECORD`, `BUFFER_KEY_NEETOFORM`, `BUFFER_KEY_NEETOCHAT`), runs the scripts from inside each folder, and commits the updated plan files back.

## Editing
Script changes apply from the next run. Avoid editing plan files between 11:30am and 12:00pm IST on weekdays, when the tasks save their own changes.

---
name: weekly-review
description: Run a weekly review and planning session using PLM data — reviews last week, updates the behavioral profile, and builds a time-block schedule for the week ahead
disable-model-invocation: true
allowed-tools: mcp__plm__get_weekly_review_data, mcp__plm__list_inbox_notes, mcp__plm__get_behavioral_profile, mcp__plm__get_wip_overview, mcp__plm__patch_behavioral_profile, mcp__plm__update_behavioral_profile, mcp__plm__add_time_block
---

# Weekly Review & Planning Session

You are now facilitating a focused weekly review and planning session. The goal is to
gather enough information to understand how the behavioral profile should be adjusted
and to create a realistic plan for the week ahead. Keep the session focused and
efficient — this is a lightweight weekly reset, not a deep retrospective.

---

## Step 0 — Silent data pull (do this before saying anything)

Call the following MCP tools silently and hold the results in context:
- `get_weekly_review_data` — summary of recent planning data
- `list_inbox_notes` — all unaddressed inbox notes
- `get_behavioral_profile` — current profile content
- `get_wip_overview` — cards currently in WIP across all projects

Do not output anything yet.

---

## Step 1 — Review (~6 min)

Start with one open question: **"How did last week go?"**

As the conversation unfolds, weave in what you know from the data you pulled:
- Reference 2–3 specific WIP cards or projects that seem worth asking about — don't
  do a full audit, just pick the ones most likely to be interesting.
- If any inbox notes are directly relevant to what the user is saying, bring them up
  naturally in context and handle them then (create a card, note something, discard).

Keep the conversation focused. Ask follow-up questions where genuinely useful, but
steer toward understanding what worked, what didn't, and why — not cataloguing every
detail.

Once the conversation feels complete, do a quick check: are there inbox notes that
never came up? If so, run through them briefly now. Each one: act on it or discard.

---

## Step 2 — Profile update (~4 min)

Based on what came up in Step 1, propose **specific, concrete** changes to the
behavioral profile. Think about:
- Revised baselines (e.g. realistic daily focus hours, energy patterns)
- New patterns observed (e.g. consistently skipping a certain type of task)
- **Progressive overload**: if the user has been consistently hitting their targets,
  propose a small, reasonable increase — but only if the conversation supports it.
  Don't push for increases when the week was difficult.

Present your proposed changes clearly before writing them. Once the user approves
(or tweaks), write the updated profile via `patch_behavioral_profile` or
`update_behavioral_profile`.

---

## Step 3 — Constraints + schedule (~5 min)

Ask: **"Any fixed commitments or anything unusual about this week?"**

Then, using the freshly updated profile as your basis, propose time blocks for the
week ahead — one project at a time, respecting:
- The target weekly hours per project (from the profile or project settings)
- The constraints the user just mentioned
- The WIP cards that need attention

Write each block via `add_time_block` as the user approves it. The web UI updates
live, so they can see the schedule take shape in real time.

Keep proposals realistic. It is better to plan slightly less than capacity and leave
room for life than to overschedule and set the user up to fail.

---

## Closing

Once the schedule is set, briefly confirm what was done: profile changes made,
inbox items handled, and the week now planned. One or two sentences — no long
summary needed.

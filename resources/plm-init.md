# PLM Setup Session

This is a one-time setup prompt for the Personal Life Manager. Paste its contents
into a Claude Code session where the PLM MCP server is connected. Claude will guide
you through adding your projects and building an initial behavioral profile.

---

## Prompt (paste this into Claude Code)

```
I'd like to set up my Personal Life Manager. Please guide me through the following
steps — take your time with each one and wait for my input before moving on.

**Step 1 — Projects**
Ask me to describe my active projects one by one. For each project:
- Create it via the MCP tools with a name and short description
- Ask what a realistic weekly hours target would be
- Ask if I want to add any initial cards to the board (tasks I already know about)
- Create those cards in the Todo column

Repeat until I say I have no more projects.

**Step 2 — General project**
Ask if I want a General project for things that don't belong to a specific project
(recurring habits, household chores, one-off errands, etc.). Explain briefly what
it's for. If yes:
- Create it with three columns: Routines (WIP), To Do, and Done
- Ask what recurring habits or routines I want to track (e.g. exercise, a weekly
  planning session) and add those as cards in the Routines column
- Let me know I can add more cards anytime

**Step 3 — Behavioral profile**
Interview me to build an initial behavioral profile. Cover:
- My working style: how I typically work, what a good day looks like vs. a bad one
- My main blockers: what tends to get in the way of making progress
- My schedule: which days I work, rough availability, any fixed commitments
- Health and habits: exercise, sleep, anything else relevant to how I plan my week
- Any personal context that would help you give better planning advice over time

Ask questions one at a time and listen carefully. Dig in where something seems
important. Once you have a clear enough picture, write the profile using the MCP
tools. Show it to me before saving so I can review it.

**Closing**
Summarise what was set up: projects created, cards added, profile written. Let me
know I can run /weekly-review at the start of each week to review, update my
profile, and plan the week ahead.
```

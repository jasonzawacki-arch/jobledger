# Job Ledger — Play Store listing copy (draft)

Drafted 2026-10-21 for §6 item 13. Not final: package id, store graphics, and the
Play-name collision check ("Job Ledger" / "JobLedger") are pickup-time items.
Data-safety answers below are provisional — regenerate from the final Play build,
not the PWA (item 13 requirement).

- **Display name:** Job Ledger (repo/brand stays "JobLedger"; per Jason 2026-10-20)
- **Privacy policy URL:** https://jasonzawacki-arch.github.io/jobledger/privacy.html
- **Contact email for listing + policy:** TBD — Jason picks at pickup
  (the policy currently says "the support email shown on the Play Store listing").

## Short description (~80 chars)

Track job applications end to end: pipeline, follow-ups, and resume matching — all on your device.

## Full description

**Your job search, kept on your phone — not on someone else's server.**

Job Ledger is a local-first job application tracker. No account, no sign-in, no cloud: your
applications live in the app on your device, and nothing leaves it except what you choose to send.

- **Pipeline board** — five stages (Saved, Applied, Screen, Interview, Offer) with touch-friendly
  card moves and a stage strip that jumps between columns.
- **Follow-up tracking that explains itself** — set a date, see it flagged in plain words; Pulse
  collects everything due into one list so nothing slips.
- **Resume-to-posting matching** — paste the posting text and see where your resume matches and where
  the gaps are. In testing it agreed with hand-written labels 80% of the time on a 25-label held-out
  set, and has never called a real gap a match (its misses lean cautious). A guide, not a guarantee.
- **Interviews and prep** — calendar links that open Google Calendar with the details filled in,
  per-job history, contacts, and prep checklists.
- **Backup and restore** — your whole dataset as one file you control.

**Free and Pro.** Free covers the full pipeline, matching, backup/restore, and calendar export for up
to 20 tracked applications (closed jobs count). One **$12.99** purchase unlocks unlimited
applications, forever — no subscription.

## Data safety (PROVISIONAL — regenerate from the final Play build)

Answers as they apply to the current `play` build target:

- **Data collected by the app:** none. No accounts, no analytics, no ads, no crash reporting.
- **Data shared by the app:** none. The only network paths in the Play build are user-initiated:
  - tapping "Add to Google Calendar" / "Add to calendar" opens a Google Calendar URL carrying the
    event details the user entered (user navigation, not app-collected data);
  - the optional one-time Pro purchase is processed entirely by Google Play (purchase records only).
- **Security practices:** data stored on-device in the app's private storage; no data transmitted to
  the developer.
- **Data deletion:** uninstall the app or clear its storage; backup files the user saved are deleted
  where the user saved them.

## Notes for pickup

- Do not use the phrase "AI-powered" anywhere in the listing (item 13).
- The eval claim above (80%, zero false matches, held-out) is the only performance number; use it as
  written or not at all (item 13).
- Collision check must test both "Job Ledger" and "JobLedger" spellings.
- Privacy policy contact line needs the real support email before the policy goes live.

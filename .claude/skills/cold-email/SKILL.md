---
name: cold-email
description: Write, rewrite or critique a cold internship email so it gets replies and reads like a real student wrote it, not an AI. Combines reply-rate research on cold outreach (length, subject lines, one ask, follow-ups, shared inboxes) with a checklist of AI-writing tells and how to remove them. Use this whenever the user wants to draft, fix, punch up, "humanize", de-AI, shorten or review a cold email, outreach email, follow-up, or a CompanyScout/`outreach` draft (e.g. "fix the 8th Light draft", "this sounds like ChatGPT", "make draft 24 pass QC", "write a follow-up for Buffer"), even if they don't say "cold email".
---

# Cold email

The goal is an email a busy engineer reads in 20 seconds, believes, and can answer with one line. Two things decide that: whether it is specific to *them*, and whether it sounds like a person. Both come from the same place, which is concrete facts only this sender could write. Generic wording is what makes an email look AI-written, *and* it is what gets it ignored.

Read `references/cold-email-research.md` for the evidence and templates, and `references/ai-tells.md` for the full tell list and rewrite examples, when you need more than the summary below.

## Inputs

Collect these before writing. Never fill a gap with an invented fact.

- **Recipient**: name and role, or a shared inbox (careers@, hello@). Small company (≲50 people): a founder can say yes. Bigger: an eng manager, or ask who to talk to.
- **Company facts**: one specific, checkable thing (a feature, a release, a blog post, a repo, an internship page). In CompanyScout this is the company's `research`; get it with `outreach company <id>`.
- **Candidate facts**: the one project closest to what they build, what it does, and how it was checked. In CompanyScout: `outreach profile show`.
- **The ask**: by default one low-pressure question about Summer 2027 engineering internships.

If a fact you want to use isn't in what you were given, leave it out or ask. A made-up detail is far worse than a plain sentence, and CompanyScout's QC rejects any claim missing from the resume or research.

## Write (or rewrite)

Structure, one short paragraph each:

1. **Opening**: the specific observation about their product or work. No greeting filler, no praise.
2. **Proof**: the one project, described by what it does and a concrete detail (a number, a method, how it was verified). Not a resume list.
3. **Relevance**: one sentence on what you could help with on *their* product (testing, tooling, evaluation, a feature area).
4. **Ask**: one question. "Is a Summer 2027 intern something your team would consider?" beats "Would you be open to a quick chat to discuss opportunities?"
5. **Sign-off**: "Larry Wang" (CompanyScout convention). At most one link. His resume PDF is attached automatically at send time, so the body doesn't need to point to it.

Constraints, with the reason for each:
- **90–140 words** in CompanyScout (its QC enforces this). Research points lower, at 50–100 for sales email, but an intern ask needs one line of proof. Stay near the low end.
- **Subject under 60 characters**, ideally 3–6 words, specific to them ("Question on Insights' AI numbers"). Mobile cuts subjects off at about 40–50 characters. Avoid "Internship application" and "Opportunity", which read as spam.
- **One ask, one question mark** (two at most). Several asks get none answered.
- **Plain text**: no bullets or bold, and paragraphs of 1–3 lines.
- **Shared inbox**: make it forwardable. The first two lines say who you are and what you want. Keep the named person in the greeting if you have one; otherwise "Hi [Company] team". End with "Who would be the right person to ask about a Summer 2027 internship?"
- **Follow-up**: same thread, 3–7 days later, under 70 words. Add one new concrete detail, don't repeat the first email, and make it easy to say no. Only one or two in total.

## De-AI pass

Go through the draft against these, strongest tells first. One stray word is noise; clusters are what readers notice.

1. **Staging**: "It's not X, it's Y" and "not just X but Y" (including split across two sentences), set-up/payoff lines, tidy one-line closers ("That's the part that excites me."), rhetorical questions.
2. **Stock email phrases**: I hope this finds you well, I'm reaching out, I came across, passionate about, excited to, I would love the opportunity, leverage, synergy, valuable insights, thank you for your time and consideration, closings that restate the ask.
3. **Inflated or vague**: generic praise ("impressive work", "innovative"), significance words (pivotal, crucial, transformative), AI vocabulary (delve, robust, showcase, foster, enhance, seamless, landscape, testament, multifaceted), "serves as" instead of "is", trailing "-ing" clauses ("…, ensuring accuracy").
4. **Rhythm by rule**: every sentence 12–20 words, triads ("fast, reliable, and scalable"), paragraphs of the same shape, several sentences starting the same way, em dashes everywhere.

Fix them by making the sentence more specific, not by adding style. Use a concrete noun or number instead of an adjective, and a plain verb (use, built, checked) instead of a fancy one. Mix a 5-word sentence in with longer ones. Cut hedges and intensifiers. Keep one detail only Larry would know (how he tested it, what broke). Read it aloud; if you wouldn't say it to an engineer in person, rewrite it.

Don't:
- add fake typos or slang
- pad for "burstiness"
- invent experiences ("we chatted at…")
- chase AI-detector scores

Detectors are unreliable: they flag plain writing by non-native speakers as AI most of the time, and the recipient is a person anyway.

## Check

Run the checker on the final text (subject on line 1, then a blank line, then the body):

```bash
python3 .claude/skills/cold-email/scripts/check_email.py draft.txt
```

It reports word count, subject length, question marks, links, stock phrases, AI vocabulary, em dashes, "not X but Y" staging and sentence-length variety. Fix every FAIL. Treat WARN as a judgment call and say why you kept it if you do.

## Output

Show the user:
1. **Subject** and **body**, ready to paste.
2. **What changed**: 2–5 bullets naming the specific problems fixed (e.g. "cut 'I hope this finds you well'", "replaced 'impressive platform' with the Insights rebuild").
3. **Checker result**: one line.
4. **Facts to confirm**: any detail the user should double-check, or that you left out for lack of a source.

## Applying a rewrite to a CompanyScout draft

Only do this when the user asks you to apply it. `outreach edit` is the one supported path: it clears approval and re-runs QC. It reads the body from whatever `$EDITOR` leaves in its temp file, so you can hand it the new body without an interactive editor:

```bash
printf '%s\n' "<new body>" > /tmp/new_body.txt
EDITOR="cp /tmp/new_body.txt" outreach edit <company_id>
```

This changes the body only. The subject is picked in `outreach review`. Never write to `outreach.db` directly, because that skips QC and the approval hash. Afterwards, show the QC result from `outreach drafts`.

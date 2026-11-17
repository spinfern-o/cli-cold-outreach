# AI-writing tells and how to remove them

Researched 2026-11-17. Main sources:
- Wikipedia, "Signs of AI writing": https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing
- Liang et al. on detector bias: https://arxiv.org/abs/2304.02819
- Kobak et al. on "delve"-style vocabulary drift: https://arxiv.org/abs/2406.07016
- the local humanizer skill (v3.1.0)

## Why "humanize" means "make specific"

- **Readers notice clusters, not single words.** One "crucial" is noise; five tells in 120 words is a pattern. People score close to chance at telling AI from human writing in general (a PNAS study found 50–52%). In email, though, they react to generic and "polished but empty" text.
- **Detectors are the wrong target.** GPTZero, Turnitin and similar tools score perplexity (how predictable the words are) and burstiness (how much sentence shape varies). On short emails they have almost nothing to measure. They also misfire: across 7 detectors, an average of 61% of human essays by non-native English writers were flagged as AI.
- **The fix that works for readers is specificity.** Concrete details only the sender could know make an email feel human and also earn replies.

## Tells, strongest first

**1. Staging (the strongest single tell)**
- "It's not X, it's Y" / "not just X, but Y", including the version split across two sentences ("This isn't about speed. It's about trust.")
- set-up/payoff and tidy one-line closers ("That's the real win.")
- rhetorical question, then the answer ("The catch? …")
- "arguing with no one" ("I'm not asking for a job, but…")

**2. Stock email phrases**
- I hope this email finds you well · I'm reaching out to · I came across your · I'm passionate about · I'm excited to · I would love the opportunity to · I'd be thrilled · leverage · synergy · valuable insights · a quick chat to discuss potential opportunities · thank you for your time and consideration · a closing that repeats the ask

**3. Vocabulary and inflation** (word lists shift with each model, so structure matters more)
- 2023–24: delve, tapestry, testament, pivotal, crucial, intricate, interplay, meticulous(ly), vibrant, landscape, underscore, boasts, garner, enduring, additionally
- later: showcase, highlight, foster, enhance, align with, robust, leverage, multifaceted, seamless, cutting-edge, innovative, game-changer
- "serves as" / "stands as" instead of "is"
- trailing "-ing" clauses: "…, ensuring every number is accurate"
- generic praise: "your impressive work", "innovative company"
- vague attribution: "experts argue"
- inflated significance: calling a class project "pivotal"

**4. Rhythm and structure**
- every sentence 12–20 words; uniform paragraphs
- triads ("fast, reliable, and scalable")
- em dashes everywhere
- bold labels or bullets in a short email
- several sentences with the same opener ("I built… I tested… I also…")
- a wrap-up paragraph restating the email

## Techniques that work

1. Use concrete nouns, numbers and names instead of adjectives.
2. Use plain verbs (use, built, checked, broke, fixed).
3. Mix sentence lengths. A 4–6 word sentence next to a 20-word one reads like speech.
4. Cut hedges (just, really, very, perhaps, I believe) and intensifiers.
5. Give one detail only the writer knows: how it was tested, what failed, what surprised them.
6. Make one small, clear ask.
7. Allow mild informality where natural: start a sentence with "But", or use a fragment.
8. Read it aloud and rewrite anything you wouldn't say to an engineer in person.
9. Every sentence must tell the reader something they didn't already know; delete the rest.

## Before → after

1. "I came across your impactful work on document processing and was excited by its potential to transform how businesses operate."
   → "I read your post on Hyperscience's handwriting model. The part about routing low-confidence fields to a person is the same problem I hit in FinDash."
2. "I'm passionate about machine learning and eager to leverage my skills in a collaborative environment."
   → "I fine-tuned YOLO11-seg on a 20-class dataset I labeled myself, and spent most of the time on why validation scores didn't match real photos."
3. "Would you be open to a quick chat to discuss potential opportunities? I would greatly appreciate any insights you can share."
   → "Is a Summer 2027 intern something your team would consider?"
4. "This isn't just a dashboard — it's a way to build trust in AI output."
   → "FinDash checks every number the model extracts against the invoice total before anyone sees it."

## Don't

- add fake typos, slang or a voice that isn't the writer's
- pad text to look more varied
- invent details (a chat that never happened, a paper not read, a project not built)
- run text through paraphrasers until it goes vague
- tune for detector scores

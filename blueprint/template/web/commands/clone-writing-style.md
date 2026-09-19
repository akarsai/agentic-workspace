---
description: Analyze writing samples (pasted or web-fetched), codify the style into STYLE.md or STYLE-<name>.md, and apply it to generated writing
argument-hint: "optional: whose style to clone (e.g. 'me', 'Benjamin Peherstorfer'), or a writing task"
---

You are cloning a writing style. Goal: text you produce matches the target
voice: the user's own, or another person's (e.g. A researcher the user
admires).

Target: $ARGUMENTS

## Step 0: Choose the style target

Ask the user (interactively) whose style to clone:

- **The user's own** → target file `/workspace/STYLE.md` (the default voice
  used when no specific style is named).
- **Someone else** (e.g. "Benjamin Peherstorfer") → ask for a short
  identifier, defaulting to the surname lowercased, and write
  `/workspace/STYLE-<identifier>.md` (e.g. `STYLE-peherstorfer.md`). Keep the
  identifier to `[a-z0-9-]`. The user may override the identifier.

If `$ARGUMENTS` names a person, skip the question and use that person. If it
names a writing task, clone the user's own style (STYLE.md).

## Step 1: Collect samples

Ask the user for writing samples, and offer web search:

1. **Ask for samples**: the user can paste text or point at files in the
   workspace (their papers, drafts, reports). Use whatever they provide.
2. **Offer web search**: ask: "Want me to also fetch samples from the web?"
   If yes, search for the target's writing yourself:
   - **Researchers**: query the arXiv API, e.g.
     `curl -s "http://export.arxiv.org/api/query?search_query=au:<surname>&max_results=5"`
     and read the abstracts from the Atom XML. For longer samples, try the
     HTML full text (`https://arxiv.org/html/<id>` or ar5iv) and strip tags
     and boilerplate.
   - **Other authors/domains**: use any accessible source (Wikipedia API,
     Semantic Scholar, Crossref, or URLs the user provides).
   - Save fetched text under `/workspace/.tmp/style-samples/<id>.txt`
     (gitignored scratch) so it is inspectable and reusable.
3. **Clean the samples**: drop references, boilerplate, and weird formatting
   so the analysis reflects the voice, not artifacts.
4. **Prefer the raw `.tex` source over the rendered text.** The source-level
   formatting (line breaks, tildes, punctuation habits, `\Cref` usage) is
   part of the style. When the user points at their own `.tex` files, analyze
   the source directly, not a PDF-to-text conversion.

## Method: Analyze → Codify → Apply → Verify

### 1. Analyze: extract a "linguistic signature"

Given the samples, produce a structured style profile. Run the analysis on
them:

> Study the tone, word choice, sentence structure, pacing, punctuation habits,
> paragraphing, and rhetorical devices in the texts below. Output a concise,
> bullet-point "style guide" that another writer could follow to reproduce
> this voice. Include do/don't rules, typical sentence length ranges,
> preferred connectors, and formatting tendencies.

This turns implicit habits into explicit rules (e.g. "prefer active voice",
"mix short and long sentences", "limit em-dashes", "avoid bullet lists unless
necessary").

**Measure, don't guess.** Quantify the hard facts from the samples and record
them in the style file: counts of banned punctuation (semicolons, em-dashes),
forbidden connectives ("whereas", "Therefore,"), sentence-length stats (mean,
median, share over 25/35 words), and the `~`-before-math density for LaTeX
prose. A measured rule is checkable. A vague one is not.

### 2. Codify: write the style file

Convert the analysis into a compact, reusable instruction block and store it
at the chosen target (`/workspace/STYLE.md` for the user's own voice,
`/workspace/STYLE-<name>.md` for someone else's. Create it if missing, extend
it if present). Keep it actionable and specific, not vague:

- **Voice & tone**: e.g. "polished but accessible. Detached observational terminology"
- **Sentence rhythm**: e.g. "average 12–18 words. Vary length. Avoid uniform long sentences"
- **Lexicon**: e.g. "simple, concrete words. Avoid jargon and filler like 'it's important to note'"
- **Formatting**: e.g. "no bullet lists unless necessary. Limit em-dashes"
- **Rhetorical moves**: e.g. "place new/important info at sentence end. Use connectors like 'but', 'that said', 'granted'"
- **Mathematical style** (research writing): formula detail, explanatory depth, LaTeX style
- **Source formatting** (LaTeX output): hard, mechanical rules: one sentence
  per source line, banned punctuation, `~` before inline math, `\Cref` for
  references. Record these as numbered, verifiable rules plus the command to
  check them (see step 4).

Append 2–3 short verbatim examples of the actual writing (80–150 words each)
at the end of the style file as few-shot demonstrations:

- Vary length (one short, one medium) to teach pacing.
- Match topic/domain when possible (tech examples for tech reports).
- Keep the author's natural quirks (fragments, colloquialisms) so it sounds human.
- Clean weird formatting so the model doesn't copy artifacts.
- For LaTeX authors, quote the `.tex` source verbatim (with `$...$` and
  `\cite` intact), not the rendered prose: the model must reproduce the
  source-level conventions.

### 3. Apply: few-shot at generation time

For every deliverable, load the style file for the voice the user wants
(`STYLE.md` for the user's own voice, `STYLE-<name>.md` for that person's)
and follow this pattern:

> Follow the stylistic rules in the style file and use the provided examples
> as your baseline for tone, rhythm, and vocabulary. Then write a [report
> type] on [topic] in this exact voice.

For LaTeX deliverables, also apply the source-formatting rules while
writing (sentence-per-line, no semicolons, `~` before math), not as a
post-processing afterthought: mechanical cleanup cannot fix voice problems.

### 4. Verify: mechanical style lint (NEW, mandatory for LaTeX)

After generating LaTeX, run the bundled linter against the output:

```bash
python3 .agents/skills/clone-writing-style/stylelint.py check <file.tex>
python3 .agents/skills/clone-writing-style/stylelint.py reflow <file.tex>   # optional normalizer
```

- `check` reports semicolons, em-dashes, and inline math without a preceding
  `~`, with line numbers.
- `reflow` rewrites prose to one-sentence-per-line and asserts that all
  display-math environments are byte-identical before/after (it aborts if any
  math changed). It is idempotent on a correctly formatted file. Use it as a
  final normalizer and as a self-test that the file already satisfies the
  line rule.
- Sanity-check idempotency on the user's own sample: `reflow` on their file
  must not change its content (only leading whitespace may differ).

Fix every reported violation before delivering. If a rule in the style file is
not checkable by the linter (e.g. banned connectives like "whereas"), grep for
it: `grep -n "whereas" <file.tex>`.

## Agentic workflow

- **Style agent**: for substantial pieces, delegate the writing to the
  `stylist` subagent and name the style file in the task: it loads that file
  (default `/workspace/STYLE.md`) + the example snippets and wraps every
  generation request with the style instructions. Tell it to follow the
  source-formatting rules while writing.
- **Reviewer agent**: before finalizing, run a second pass with the
  `style-reviewer` subagent against the same style file: it checks the output
  (flag excessive em-dashes, bullet-list overuse, measure average sentence
  length, check tone and lexicon) and reports violations. For LaTeX output,
  also run the mechanical lint of step 4 and fix what it flags. The reviewer
  checks *voice*. The linter checks *mechanics*. Both are required.
- **Distillation loop**: collect failure cases where the output violated the
  style, diagnose which procedural step was missing, and add a short advisory
  rule (50–60 tokens) to the relevant style file. Include *mechanical*
  failures (semicolons, line wrapping, missing tildes): when the linter
  catches a recurring violation, promote it to a numbered hard rule in the
  style file. Over time this living document improves consistency without
  fine-tuning.

## Usage

- `clone-writing-style`: interactive: choose the target (own or a named
  person), collect samples (pasted and/or web-fetched), write the style file.
- `clone-writing-style Benjamin Peherstorfer`: same, for that person →
  writes `/workspace/STYLE-peherstorfer.md`.
- On every substantial deliverable afterwards: load the relevant style file,
  use `stylist` for writing and `style-reviewer` for the final pass, run the
  mechanical lint for LaTeX, and keep the style file updated via the
  distillation loop.

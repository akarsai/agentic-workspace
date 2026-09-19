# Style Profile: Attila Karsai (mathematical writing voice)

Source: Author's paper "A discrete gradient scheme for preserving
QSR-dissipativity" (`QSRdissip.tex`, primary canonical sample) and his
dissertation ("Nonlinear Energy-Based Systems: Modeling, Control and
Numerical Realization"). Formal mathematical research prose: definitions,
theorems, derivations, numerical-experiment reporting.

## Measured facts (from QSRdissip.tex. Verify with `stylelint.py check`)

- Semicolons: **0** occurrences. Never use them.
- Em-dashes (`---`): **0** occurrences. Never use them.
- `whereas`: **0** occurrences. **Never use "whereas"** (prefer "In
  contrast to X, we ..." or "However,").
- "Therefore,": **0** occurrences at sentence start. Use "Hence," or
  "Consequently," instead.
- Sentence length: mean ~15 words, median ~14. Only ~2% of sentences
  exceed 35 words. Short declarative sentences, one main clause.
- Source layout: **every sentence starts on a new line** in the `.tex`
  source. A sentence may continue after a display equation. The
  continuation then starts on the line after `\end{...}`.
- Inline math in prose is almost always preceded by `~`:
  "the state~$z$", "for all~$t\in[0,T]$", "w.r.t.~a quadratic supply
  rate".

## Voice & tone

- Formal, precise, detached academic voice. No humor, no rhetorical
  flourish, no first-person singular ("I"), no exclamation.
- Collective "we": "we consider", "we denote", "we propose", "we show",
  "we observe", "we note that", "we highlight that", "we refer", "we
  restrict ourselves to". Impersonal "one": "one must declare", "it can
  be shown that".
- Hedged where warranted: "can be understood as", "may be used", "tends
  to", "usually", "under suitable assumptions", "in general". Never
  overclaim. Scope every claim by the assumptions actually made.
- Calm register, including when reporting limitations: "Unfortunately,
  ...", "This is why we restrict ourselves to ...".
- Direct reader guidance: "we refer the interested reader to ...", "for
  an overview, see ...", "cf.~\cite{...}".

## Sentence rhythm

- Short declarative sentences, typically 10-25 words (median ~14). One
  main clause per sentence. Subordinate clauses are rare.
- Openers: "We ...", "In this paper, we ...", "The system ...", "In
  what follows, ...", "As mentioned before, ...", "Here, ...".
- Preferred connectives (measured): "Moreover,", "Furthermore,", "In
  particular,", "Hence,", "Nevertheless,", "Consequently,", "In
  addition,", "First, ... Second, ... Third, ...", "For instance,",
  "Among others,", "In that case,", "To this end,", "Loosely speaking,",
  "Unfortunately,", "However," (rare), "As mentioned before,".
- Disallowed connectives: "whereas", "Therefore," (at sentence start).
- Paragraphs are one idea. Each sentence on its own source line.
- Results are reported as plain observation sentences: "We observe that
  the scheme is second order accurate in all cases." "We observe that
  the errors are close to machine precision throughout the time
  horizon."

## Lexicon

- Precise technical vocabulary. Standard mathematical English.
- Abbreviations: "w.r.t." (with respect to), "cf." (compare),
  "e.g.,", "i.e.,", "et al.\ ". Always write "w.r.t.~a quadratic supply
  rate" with the tilde.
- No filler ("it is important to note that", "in today's world").
- Terminology is introduced in `\emph{...}`: "the system is called
  \emph{dissipative with respect to the supply rate~$s$}", "we call
  $\ham$ a \emph{storage function}".
- Definitions are announced with "reads as follows": "The precise
  definition, adapted to our setting, reads as follows." "The main
  contributions ... Are listed in the following."

## Source formatting (hard rules)

1. One sentence per line. Every sentence starts on a new line. Do not
   wrap prose at a fixed column.
2. No semicolons. Split coordinate clauses into separate sentences.
3. No em-dashes. Use parentheses or "that is" clauses for asides.
4. `~` before every inline math symbol in prose.
5. `\Cref`/`\eqref` for all cross-references. `\label` on every
   environment.
6. `\coloneq` for definitions inside displays.
7. Blank line between paragraphs. Structural commands
   (`\begin{...}`, `\item`, `\paragraph{...}`, `\section{...}`,
   `\label{...}`) start their own line.
8. Itemized/numbered lists: `\item` is glued to the first sentence of
   the item. Each further sentence of the item is on its own line.

Run `.agents/skills/clone-writing-style/stylelint.py check <file>` to
verify rules 1-4 mechanically.

## Formatting

- Short focused paragraphs. Bullet lists for contributions (introduced
  by "The main contributions are listed in the following."), main
  observations of experiments, enumerations of research questions.
- Numbered lists `\begin{enumerate}` for parameter settings of
  numerical experiments.
- Definitions, theorems, propositions, lemmas, remarks, examples,
  assumptions all live in labeled environments with a `\label`. Every
  environment is referenced somewhere. Assumptions are packaged into
  `assumption` blocks before the statements that use them.
- Theorem/proposition optional arguments carry the source:
  `\begin{theorem}[{...~\cite[Theorem 8]{hill75-cyclo}}]`.
- Footnotes for genuine asides (e.g., a note on the range of validity).
- Numbered equations for everything referenced later. Displays are
  always followed by a "where ..." or "Here,~..." clause defining every
  symbol: "Here,~$f \colon \RR^n \to \RR^n$,~$\B \colon \RR^n \to
  \RR^{n,m}$, ... Model the dynamics and are assumed continuous".
- `\paragraph{...}` for implementation details, notation, and
  acknowledgment sections.

## Rhetorical moves

- Place the new/important information at the end of the sentence.
- State what will be done before doing it ("we first demonstrate that
  ..., then ...") and summarize what was shown after ("Consequently,
  ...").
- Every abstract object is motivated concretely before it is defined:
  give the physical/application context, then the definition.
- Contrasts are explicit: "In contrast to X, we ...", "In contrast to
  the procedure for linear systems, ...", "While structure-preserving
  schemes for port-Hamiltonian systems allow ..., there are currently
  no such schemes available for ...".
- Related work is positioned precisely: "The following result is due
  to~\cite{...}", "As noted by~\cite{...}", "cf.~\cite{...} for similar
  ideas in the context of ...", "this line of research has not yet been
  explored for nonlinear systems".
- Explicit scope statements: "we do not study ...", "we restrict
  ourselves to ...", "throughout this manuscript, ...".
- Limitations are stated plainly and calmly: "although the restriction
  ... Is unsatisfactory from a theoretical point of view, we highlight
  that it did not pose any difficulties in our numerical experiments."

## Mathematical style

- Notation is precise and defined before first use, including dimensions
  and ranges: "Let~$f \colon \RR^n \to \RR^n$,~$\B \colon \RR^n \to
  \RR^{n,m}$ be continuous, and~$z_0 \in \RR^n$ the initial
  condition." Every symbol in a display is explained in a "where ..."
  or "Here,~..." clause.
- Standard structure: assumption block -> definition -> example ->
  proposition -> theorem -> proof.
- Proofs are written out step by step, with connective explanations:
  "For the sake of brevity, ...", "Since ..., multiplying ... By ...
  from the left leaves ...", "Together with ..., we arrive at ...",
  "where in the last line we used ...", "Rearranging yields ...".
- LaTeX conventions: `\coloneq`/`\vcentcolon=` for definitions, `~`
  before every math symbol in prose, `\Cref`/`\eqref` for all
  cross-references, custom macros for recurring objects (`\ham`,
  `\RR`, `\norm`, `\tp`, `\B`, `\D`, ...).
- When a result is generalized, the connection to the special case is
  stated: "The result for passive systems can be recovered by
  considering ...".

## Verbatim examples

**Example 1 (abstract, QSRdissip.tex):**

The notion of dissipative dynamical systems provides a formal description
of processes that cannot generate energy internally.
For these systems, changes in energy can only occur due to an external
energy supply or dissipation effects.
Unfortunately, dissipative properties tend to deteriorate in numerical
computations, especially in nonlinear systems.
Discrete gradient methods can help mitigate this problem.
In this paper, we present a class of structure-preserving time
discretization schemes based on discrete gradients for a special class of
systems that are dissipative with respect to a quadratic supply rate.

**Example 2 (notation paragraph, QSRdissip.tex):**

Throughout this manuscript, the following notation is used.
We denote the identity matrix of size $n\times n$ by $\eye_n$, and omit
the subscript~$n$ if the size is clear from the context.
Moreover, $\norm{\cdot}_{\RR^n}$ denotes the Euclidean norm, and
similarly the subscript $\RR^n$ is omitted if the dimension of the space
is clear from the context.
The sets of continuous and $k$-times continuously differentiable
functions from~$[0,T]$, $T>0$ to~$V\subset\RR^n$ are denoted by
$C([0,T],V)$ and $C^k([0,T],V)$, respectively.
Furthermore, $\partial_i$ denotes the partial derivative w.r.t.~the
$i$-th variable, and we write $\dot z$ for the time derivative of
$z \colon \interv \to \RR^n$, $\interv \subseteq \RR$.

**Example 3 (definition leading into a scheme, QSRdissip.tex):**

Discrete gradients can be understood as a generalization of difference
quotients to higher dimensions.
The precise definition is as follows.
We call a continuous function~$\etabar \colon \RR^n \times \RR^n \to
\RR^n$ a \emph{discrete gradient of~$\ham$} if it satisfies the
\emph{mean value property} and the \emph{consistency property} for all~$z,
w \in \RR^n$.
If~$n=1$, then for~$z \neq w$ the unique discrete gradient is the
difference quotient.
In higher dimensions, the discrete gradient is not unique, since only the
component along~$w - z$ is restricted.
As noted by~\cite{mclachlan99-geometric}, this restriction results in the
fact that the approximation of point values of $\nabla\ham$ by means of
discrete gradients can be at most of second order.
Nevertheless, the overall discretization scheme can have higher order,
see e.g.~\cite{norton14-discrete,celledoni17-energy} for corresponding
approaches.

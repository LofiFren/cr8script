# SWOT analysis — cr8script v1.1

Grounded in what we actually built (the interpreter, the tour, the load tester),
not the aspirational pitch. v1.0 baseline updated for v1.1 shipping work; entries
that v1.1 closed are marked **(addressed)**.

## Strengths

- **Honest errors with hints.** Every runtime error names the line, the value, and a
  one-line fix suggestion. We caught real Python footguns the language refuses to
  do silently: `"5" + 3`, `if 1 then`, `let pi = ...; pi = ...`, `record.missing_field`.
  The error catalogue is the most defensible piece of the design.
- **Pipelines with auto-bound record fields.**
  `things | where age is at least 18 | group by region | summarize { total: sum(amount) }`
  reads like SQL and matches the target audience's mental model. Few small
  languages get this right.
- **Single-file, single-host.** `python3 cr8script.py file.cr8` runs everywhere
  Python 3.9+ runs. No environment, no pip, no build. The whole interpreter is
  ~2.3k lines — vendorable, auditable, and teachable from source.
- **Soft keywords.** Letting common English nouns like `times`, `with`, `each`, `by`
  be variable names is unusual and pays off — the load tester uses
  `let times = ...` naturally.
- **Determinism.** No metaclasses, no operator overloading, no decorators, no
  monkey-patching. What you see is what runs. Reviewers and beginners both benefit.
- **Decimal numbers by default.** `0.1 + 0.2 = 0.3` exactly, money math doesn't
  drift, and the language can be honest about principle #1 ("one number type")
  without a footnote.
- **LLM-shaped feedback loop.** `--check-json` produces structured diagnostics
  (line, message, hint) before execution. A model can iterate against the
  static checker without running anything.

## Weaknesses

- **(addressed)** ~~The standard library is tiny~~. v1.1 ships `json` and `csv`
  alongside `http`, `time`, `math`. Still missing: `regex`, `file`, `dates`.
- **Tree-walking interpreter is slow.** ~1000 sequential rps on localhost — fine
  for a demo, hopeless for any compute-heavy script. No bytecode, no JIT, no
  concurrency. The load tester is sequential, so it's a poor *load* tester.
- **(addressed)** ~~Float-only numbers leak~~. v1.1 switched to Python `Decimal`.
  Default precision is 28 digits; ranges/indices reject non-integer Decimals.
- **No editor support.** No LSP, no syntax highlighting, no formatter. The
  `--check-json` output is half of what an LSP would surface, but no editor
  consumes it yet. Adoption barrier for human users.
- **(partially addressed)** ~~Errors are runtime-only~~. v1.1 ships a static
  field-access checker (`--check`/`--check-json`) that catches `r.nmae` against
  records bound from literals. It does *not* yet check unknown name references
  inside pipeline stages, or types beyond record-shape.
- **(addressed)** ~~No string interpolation~~. v1.1 ships `f"hello {name}"`
  with `{{` / `}}` escaping. The load tester can shed its `+ to_text(...)`
  chain.
- **`if` is now both statement and expression.** Same syntax, position-dependent
  meaning. Defensible (Rust does it), but trips the "one way to do it" principle
  slightly.

## Opportunities

- **LLM-assisted scripting tailwind.** A small language with no surface area is
  *easier* for an LLM to write correctly than Python. As natural-language → code
  becomes the default for non-programmers, languages that LLMs rarely get wrong
  have a structural advantage.
- **Browser playground via WASM.** Compile the interpreter to WASM, host a
  playground at a single URL. Zero install, instant share-by-link. For an
  audience that doesn't have Python set up, this is the highest-leverage move
  available.
- **Embedded scripting niche.** Lua and Wren are the incumbents for "embed a
  sandboxed scripting language." A more readable option with strict semantics
  could land in config-as-code, game logic, rules engines, automation runners.
- **Spreadsheet/notebook adjacency.** Power Query M, Notion formulas, Airtable
  scripting are all bespoke per-app DSLs. A neutral cr8script that runs
  alongside spreadsheets — pipelines + records map cleanly to row/column
  operations — could be a wedge.
- **Education.** Plain English keywords + honest errors + no install make this a
  credible "first text-based language after Scratch." Schools and bootcamps are
  a real market.

## Threats

- **The simple-language graveyard.** Logo, BASIC, Boo, V, Squirrel, dozens more.
  Without a corporate sponsor or a viral hook, "yet another small language"
  plateaus at <100 users and stays there. Adoption, not implementation, is the
  hard problem.
- **Python is closing the gap.** Python 3.11+ has dramatically better error
  messages. Type checkers (pyright, mypy) catch more at edit time. The "Python
  is hostile to beginners" pitch is weaker than it was five years ago.
- **Domain DSLs eat the niche per-app.** Notion formulas, Airtable scripts,
  Retool's JS, Zapier paths — each one captures the casual user *inside the app
  they're already using*. A standalone language has to drag users out of their
  workflow.
- **No ecosystem flywheel.** Python's moat is NumPy/pandas/scikit-learn, not the
  syntax. cr8script would need years of domain library work to be useful where
  Python is, and the audience won't wait.
- **The "non-programmer adult" segment is fragmented.** Analysts use SQL +
  spreadsheets; scientists use Python + R + MATLAB; hobbyists use whatever their
  YouTube tutorial uses. There's no single beachhead — winning each one requires
  different libraries and integrations.

## Net read

cr8script v1 is a credible *teaching artifact* and a defensible *embedded
language candidate*. It is **not** yet credible as a daily driver for the stated
audience — the standard library is too small and there's no editor support.

The honest path forward is to pick **one** wedge (browser playground for
education, **or** embedded-config replacement for Lua, **or** a notebook
frontend) and invest narrowly, rather than chase Python head-on.

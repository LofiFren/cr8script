# AGENTS.md -- running cr8script inside an LLM agent

Drop this file (or excerpts from it) into your agent's system prompt or
tool-use loop to teach a model cr8script in one shot. It is the
operational counterpart to [LLMS.md](LLMS.md): LLMS.md is the language
reference; this file is the playbook for **using cr8script as an
agent's scripting target**.

## Why an agent should pick cr8script

When a model writes a quick script -- to ingest JSON, summarize a list,
emit a report, or compute a financial total -- the failure modes are predictable:

- silent type coercion (`"5" + 3` "works" in Python, then later math goes wrong)
- silent decimal-to-float drift (`0.1 + 0.2` == `0.30000000000000004`) -- silently corrupts totals in any finance, billing, or tax calculation
- truthy/falsy surprises (`if 0 then` skipped, `if [] then` skipped)
- typo'd field name on a record (silent `None`)
- 0-vs-1 indexing confusion across languages
- big monolithic Python error text the model has to regex over

cr8script flips each of those into a **located, structured, hinted
error** the model can fix in one tool call:

```json
{ "severity": "error", "line": 23, "message": "record has no field `amout`",
  "hint": "did you mean `amount`? available: amount, customer, id" }
```

The static checker (`--check-json`) walks the AST and emits **all such
issues at once**, so one round trip through the agent's tool-use loop
typically converges to a working program.

## The loop

```text
1. Generate a .cr8 script.
2. Run:  python3 cr8script.py --check-json $FILE
3. Parse stdout as JSON.
   - If the list is empty:   continue to step 4.
   - Otherwise:               for each {line, message, hint},
                              edit the file at `line` using `hint`,
                              then GOTO 2.
4. Run:  python3 cr8script.py $FILE
   - exit 0:   done.
   - exit 1:   parse line/message/hint from stderr, edit, GOTO 2.
```

A working end-to-end demo is at [`examples/agent_loop/`](examples/agent_loop/).

## System-prompt template (copy-paste)

> You are writing cr8script, not Python. cr8script is an English-shaped
> scripting language with **honest types** (no truthy/falsy, no silent
> coercion), **decimal numbers** (`0.1 + 0.2` is exactly `0.3`),
> **1-based lists**, and **`end`-terminated blocks**. Function syntax is
> `to name(args) ... end`, **not** `def name(args):`.
>
> Before claiming a script works, run
> `python3 cr8script.py --check-json file.cr8` and fix every issue in
> the JSON output. Each issue has `{line, message, hint}` -- apply the
> hint, re-check, repeat until the list is empty. Then run the script.
>
> Hard rules -- these are errors, not warnings:
>
> - Use `is`, `is not`, `is greater than`, `is less than`, `is at
>   least`, `is at most`. **Never** `==`, `!=`, `>=`, `<=`.
> - Lists are 1-based. `xs[0]` is an error. Use `xs.first` / `xs.last`
>   or `xs[1]`.
> - `if` requires a real boolean. `if 0 then`, `if "" then`,
>   `if [] then` are all errors.
> - Records are values. `r.field` requires the field to exist. Use
>   `r.get("key")` for safe lookup (returns `nothing` on miss).
> - Only `nothing` for absence -- no `null`, `None`, `undefined`.
> - `let` is immutable. Reassignment requires `var`.
> - Pipelines (`|`) only operate on lists. Verbs are `where`, `sort
>   by`, `take`, `map`, `group by`, `summarize`. Inside `where` /
>   `sort by` / `map`, bare names auto-resolve to fields of each item.
> - `show` is a statement, no parens: `show "hi"`.
>
> The full reference is in `LLMS.md`. The end-to-end self-correction
> loop demo is in `examples/agent_loop/`.

(That block is ~30 lines and ~370 tokens -- small enough to live
permanently in a system prompt.)

## Tool definitions

Two tools are usually enough. Names match common Claude / OpenAI
schemas; adapt as needed.

### `cr8_check`

Runs the static checker and returns the JSON output. Always run this
**before** `cr8_run`.

```jsonc
{
  "name": "cr8_check",
  "description": "Static-check a cr8script file. Returns a JSON list of issues. Empty list = clean.",
  "input_schema": {
    "type": "object",
    "properties": { "path": { "type": "string" } },
    "required": ["path"]
  }
}
```

Implementation:
```sh
python3 /path/to/cr8script.py --check-json "$path"
```

### `cr8_run`

Runs the script and returns stdout. Stderr (one line: `<file>:
error (line N): <msg> hint: <hint>`) means a runtime issue; feed it
back and edit.

```jsonc
{
  "name": "cr8_run",
  "description": "Run a cr8script file. Returns stdout on success or a single-line error with line/message/hint.",
  "input_schema": {
    "type": "object",
    "properties": { "path": { "type": "string" } },
    "required": ["path"]
  }
}
```

Implementation:
```sh
python3 /path/to/cr8script.py "$path"
```

## Canonical examples for context

If your model is small and you want to seed it with idioms in-context,
include one of these -- pick by task shape:

| Task | Read first |
|------|------------|
| Anything new | [`examples/tour.cr8`](examples/tour.cr8) -- the whole language in 92 lines |
| Self-correction loop demo | [`examples/agent_loop/`](examples/agent_loop/) -- broken -> diagnostics -> fixed |
| Plan before code (typed task graph) | [`LLM_MAP.md`](LLM_MAP.md) + [`examples/llm_map/`](examples/llm_map/) -- a typed planning artifact that sits between prompt and `.cr8` generation; its own validator (`tools/check_map.py`) follows the same `--check-json` shape |
| Fetch JSON, transform, write CSV | [`examples/api_ingest.cr8`](examples/api_ingest.cr8) |
| Validate a list of records | [`examples/validate.cr8`](examples/validate.cr8) |
| Emit a markdown report | [`examples/report_md.cr8`](examples/report_md.cr8) |
| Generate static HTML / SVG | [`examples/make_game.cr8`](examples/make_game.cr8), [`examples/make_mindmap.cr8`](examples/make_mindmap.cr8) |

## What cr8script is not for

Don't push the model toward cr8script for:

- **File I/O.** Out of scope (yet). Pipe data in via `http.get` or
  hard-code; pipe data out via stdout to redirected files.
- **Long-running services.** No async, no concurrency. cr8script is
  for one-shot scripts.
- **Heavy compute.** Tree-walking evaluator. Scripts that touch >10⁵
  records will be slow.
- **Wrapping huge dependency surfaces.** No imports. The built-ins
  list (`math`, `http`, `time`, `json`, `csv`) is the surface.

For those, your agent should produce Python or a real shell pipeline
instead. cr8script's win is **per-script reliability** -- the loop
above terminates fast when the task fits.

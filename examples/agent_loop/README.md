# agent_loop — the self-correction loop, end-to-end

This is the canonical demo of cr8script's value proposition for LLM
agents: the **static checker emits structured JSON, the model fixes
each issue, the program runs**. No human in the loop.

## Files

| File | What it is |
|------|------------|
| `broken.cr8`       | Three classic typos a Python-trained LLM will produce |
| `diagnostics.json` | The actual `--check-json` output for `broken.cr8` (committed so you can read it without running anything) |
| `fixed.cr8`        | The corrected version — passes the checker, runs cleanly |

## The loop, step by step

### 1. The model writes `broken.cr8`

Three field-name typos (`cusomer`, `amout`, `custmer`) — exactly the kind
of mistake a model makes when it's distracted, autocompleting from a
weak prior, or pattern-matching against another language.

### 2. Run the static checker first — never run untested code

```sh
python3 cr8script.py --check-json examples/agent_loop/broken.cr8
```

Returns a JSON list (one entry per issue) and exits non-zero. The full
pretty-printed output is in `diagnostics.json`. Each entry has:

```json
{
  "severity": "error",
  "line": 20,
  "message": "record has no field `cusomer`",
  "hint": "did you mean `customer`? available: amount, customer, id"
}
```

Three things make this useful for an agent:

- **Structured.** The model parses JSON, not regex over English error text.
- **Located.** `line` lets the model `Edit` precisely.
- **Suggestive.** `did you mean` collapses the search space — the model
  doesn't have to re-derive the field name from context.

### 3. The model applies the hints

For each issue: open the file at `line`, replace the bad identifier
with the suggested one. With 3 issues all of the form "did you mean X",
this is mechanical — no creative leap required.

### 4. Re-check until clean

```sh
python3 cr8script.py --check-json examples/agent_loop/fixed.cr8
# []
```

Empty list, exit 0. Now — and only now — run the program:

```sh
python3 cr8script.py examples/agent_loop/fixed.cr8
# first customer: Ada
# big orders: 2
# labels: [Ada, Bob, Carlos]
```

## Why this matters

A typical Python error from a typo'd field is:

```
AttributeError: 'dict' object has no attribute 'cusomer'
```

Unlocated, unsuggestive, and always one bug at a time (the program
crashes on the first). cr8script's checker reports **every** bug in
one pass, located, with a hint — which means a single round trip
through the agent's tool-use loop converges to a working program.

## What the checker does NOT catch

The static checker is conservative — it tracks shapes for record
literals and lists of record literals bound by `let`. It will **not**
flag:

- Field typos on function parameters (the param's shape is unknown)
- Field typos on values returned from arbitrary expressions
- Runtime errors like `"5" + 3` (caught when the line executes)
- Logic errors (the script does the wrong thing, not the wrong syntax)

For those, the runtime error stays small and located:

```
file.cr8: error (line 19): `name` is not defined
  hint: did you mean to write `let name = ...` first?
```

Same shape (line + message + hint), same loop — just one bug per run
instead of all-at-once.

## Embedding this in your agent

A minimal harness:

```text
1. Generate a .cr8 script.
2. Run:  python3 cr8script.py --check-json $FILE
3. If output != "[]":
     parse JSON, edit each {line, message, hint}, GOTO 2.
4. Run:  python3 cr8script.py $FILE
5. If exit != 0:
     parse the line/message/hint from stderr, edit, GOTO 2.
6. Done.
```

The whole point of cr8script's small surface area is that this loop
**terminates fast** — usually one iteration.

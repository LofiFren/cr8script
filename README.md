# cr8script

A simpler scripting language for LLMs and anyone who wants quick scripts
without Python's footguns — analysts, scientists, hobbyists, and the models
writing code on their behalf.

## Run it

```bash
python3 cr8script.py examples/hello.cr8       # run a file
python3 cr8script.py examples/tour.cr8        # the language tour
python3 cr8script.py                           # start the REPL
python3 cr8script.py --test                    # run the golden test suite
python3 cr8script.py --check file.cr8          # static checks (typos in field access)
python3 cr8script.py --check-json file.cr8     # same, JSON output (LLM correction loop)
python3 cr8script.py --lex file.cr8            # dump tokens (debugging)
python3 cr8script.py --ast file.cr8            # dump the AST  (debugging)
```

## Principles

1. **One way to do it** — single loop, single function syntax, single number type.
2. **No invisible syntax** — blocks end with `end`. No significant whitespace.
3. **Reads like English** — `is greater than`, `is at least`, `for each`, `where`.
4. **Strong, honest types** — no truthy/falsy, no silent `"5" + 3`.
5. **Immutable by default** — `let` is forever; `var` to opt into change.
6. **No null surprises** — only `nothing`. Indexing a missing key returns `nothing`,
   typing the wrong field name is a hard error with a "did you mean" hint.
7. **Values, not objects** — records and functions, no classes, no `self`.
8. **Pipelines for data** — `things | where age >= 18 | group by region | summarize {...}`.
9. **Batteries built in, no imports** — `math`, `json`, `csv`, `http`, `time`,
   plus `length`, `sum`, `to_text`, `to_number` at top level.
10. **Errors that teach** — every error names the line, the value, and a hint.
11. **First-class lists of records** — pipeline verbs auto-bind record fields as locals.
12. **Determinism over cleverness** — no metaclasses, no operator overloading,
    no decorators, no monkey-patching.
13. **Honest decimal math** — `0.1 + 0.2` is exactly `0.3`; no float drift.
14. **LLM-shaped feedback** — `--check-json` emits structured (line, message, hint)
    diagnostics so a model can self-correct before running.

## A taste

```
let sales = [
  { product: "widget", region: "east", amount: 12.50 },
  { product: "gadget", region: "east", amount:  8.00 },
  { product: "widget", region: "west", amount: 15.00 },
  { product: "doodad", region: "east", amount: 99.00 },
]

let by_product = sales
  | group by product
  | summarize { total: sum(amount), n: length(items) }
  | sort by total descending

for each row in by_product
  show f"{row.product}: {row.total} across {row.n} order(s)"
end
```

## Status

v1.1: lexer, parser, tree-walking evaluator, REPL, static checker, ten golden
tests. Single-file Python implementation (`cr8script.py`, ~2.3k lines). The
*language* is independent of Python — only the bootstrap interpreter is in
Python, and could be reimplemented in any host.

Built-ins shipped: `math`, `http`, `time`, `json`, `csv`. Numbers are decimal
by default. Pipelines support `where / sort by / take / map / group by /
summarize`. Strings support `f"..."` interpolation.

Out of scope for now (defer until concrete pull): table literal syntax, modules
/ imports, async / concurrency, regex, file I/O. **Not** on the roadmap:
classes (principle #7).

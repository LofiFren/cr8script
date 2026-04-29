# LLMS.md -- cr8script reference for language models

A condensed reference for any LLM asked to read or write `.cr8` code.
Humans should read `README.md` first; this file is a rules-and-grammar
sheet, no narrative.

## What cr8script is

A small interpreted language with one file (`cr8script.py`). Tree-walking
evaluator, decimal numbers, English-shaped syntax, no imports. Runs as
`python3 cr8script.py file.cr8`. **The language is not Python** -- Python
idioms produce errors, not silent success.

## Hard rules -- DO NOT do these

- **No `def`, no `:`, no significant whitespace.** Functions use `to name(...)
  ... end`. Blocks end with the keyword `end`.
- **No truthy/falsy.** `if` requires a real boolean. `if 0 then` is an error,
  not "false".
- **No silent type mixing.** `"5" + 3` is an error. Convert with `to_text(3)`
  or `to_number("5")`.
- **No cross-type equality.** `5 is "5"` is an error, not `false`.
- **No `==`, `!=`, `>=`, `<=`, `**`, `//`.** Spell comparisons in English:
  `is`, `is not`, `is greater than`, `is less than`, `is at least`,
  `is at most`. Power is `math.pow(x, y)`.
- **List indices start at 1.** `xs[0]` is an error.
- **Dot access on a record requires the field exists.** Typos are hard
  errors with a "did you mean" hint. Use `record.get("key")` for safe lookup
  (returns `nothing` on miss).
- **Ranges need whole numbers.** `1.5..3.5` is an error.
- **`let` is immutable.** Reassignment requires `var`.
- **Pipelines only operate on lists.** Convert first if needed.
- **Only `nothing` for absence.** No `null`, no `None`, no `undefined`.

## Syntax cheat sheet

```
# Comments. Only one comment style.

# --- bindings ---
let name = "Ada"            # immutable
var counter = 0             # mutable
counter = counter + 1

# --- conditionals (statement form) ---
if score is greater than 10 then
  show "high"
else if score is 0 then
  show "zero"
else
  show "other"
end

# --- conditionals (expression form, returns a value) ---
let label = if x is at least 18 then "adult" else "minor" end

# --- comparisons ---
x is 5                      # equal
x is not 5
x is greater than 5
x is less than 5
x is at least 5             # >=
x is at most 5              # <=
x is nothing
x is not nothing

# --- arithmetic ---
+ - * /                     # standard
mod                         # remainder, e.g. 17 mod 5

# --- logical (booleans only) ---
and  or  not

# --- loops ---
for each n in 1..5
  show n
end

repeat 3 times
  show "tick"
end

# --- ranges ---
1..5                        # [1,2,3,4,5] inclusive, whole numbers only
range(5)                    # [1,2,3,4,5]
range(2, 6)                 # [2,3,4,5,6]

# --- functions ---
to greet(person)
  return "hello, " + person.name
end
let f = greet               # functions are values
f(ada)

# --- records (no classes, no methods on user records) ---
let ada = { name: "Ada", age: 36 }
ada.name                    # field access; missing field is an error
ada.get("name")             # safe lookup, returns nothing on miss
ada.has("age")              # boolean
ada.keys                    # property, no parens
ada.with({ age: 37 })       # returns new record (immutable update)
ada.with("age", 37)         # single-field form

# --- lists (1-based) ---
let xs = [10, 20, 30]
xs[1]                       # 10
xs.first                    # 10  (property)
xs.last                     # 30
xs.length                   # 3
xs.reverse                  # [30, 20, 10]
xs.contains(20)             # true
xs.join(", ")               # only on lists of text

# --- text ---
"hello".upper               # property
"  hi  ".trim
"abc".length
"abc".contains("b")
"a,b,c".split(",")
"hello".starts_with("he")
f"hello {name}"             # interpolation. Escape: {{ }}

# --- maybe / nothing ---
let v = some_lookup
if v is nothing then
  show "absent"
else
  show v
end

# --- error handling ---
try
  let n = to_number("nope")
otherwise as err
  show err.message          # err is { message, line }
end
```

## Built-ins (top level -- no imports)

- `show <expr>` -- print. **Statement keyword, no parens.**
- `length(x)`, `count(list)`, `sum(list)`, `average(list)`, `min(list)`, `max(list)`
- `to_text(x)`, `to_number(text)`
- `range(end)`, `range(start, end)`
- `keys(record)`, `type(value)`, `assert(cond, message?)`
- `args` -- list of CLI arguments

## Modules (accessed as `module.member`)

- `math.sqrt`, `math.abs`, `math.floor`, `math.ceil`, `math.round`,
  `math.pow`, `math.pi`, `math.e`
- `time.now()`, `time.monotonic()`, `time.sleep(seconds)`
- `http.get(url)` -- returns `{ ok, status, body, time_ms, error }`.
  `ok=true` means a response arrived (any status). `ok=false` is
  connection-level failure only. The script decides what counts as
  success (typically `r.ok and r.status is at least 200 and r.status is less than 400`).
- `json.parse(text)`, `json.stringify(value)`
- `csv.parse(text)`, `csv.write(rows)`

## Pipelines

```
let people = [
  { name: "Ada", age: 36 },
  { name: "Bob", age: 17 },
]

# Inside `where`, `sort by`, and `map`, bare names auto-resolve to fields
# of each item. Use `it` for non-record items.
let adults = people
  | where age is at least 18
  | sort by age descending
  | take 5
  | map name

# group by + summarize:
let by_region = sales
  | group by region
  | summarize { total: sum(amount), n: length(items) }
  | sort by total descending
```

Verbs (these are the only ones):

- `where <expr>` -- filter
- `sort by <expr>` (optional `ascending` / `descending`)
- `take <n>`
- `map <expr>`
- `group by <expr>` -- yields one record per group with two fields:
  `items` (the list of grouped rows) and the group key. The key field
  is **named after the grouping expression when it's a bare name** --
  `group by region` produces `{ region, items }`, not `{ key, items }`.
  Complex expressions fall back to `key`: `group by name.upper`
  produces `{ key, items }`.
- `summarize { ... }` -- runs after `group by` (or directly on a list
  of records). Bare names inside resolve against `items` -- e.g.
  `sum(amount)` sums the `amount` field of grouped items, and
  `length(items)` is the per-group count.

## Soft vs hard keywords

**Soft** (usable as variable names): `each`, `times`, `by`, `as`,
`descending`, `ascending`, `with`, `from`, `of`.

**Hard** (reserved): `let`, `var`, `if`, `then`, `else`, `end`, `for`,
`in`, `repeat`, `to`, `return`, `try`, `otherwise`, `true`, `false`,
`nothing`, `and`, `or`, `not`, `mod`, `is`, `greater`, `less`, `than`,
`at`, `least`, `most`, `show`, `where`, `sort`, `take`, `map`, `group`,
`summarize`.

## Self-correction loop

Before claiming a `.cr8` file works:

```
python3 cr8script.py --check-json file.cr8
```

emits structured `{ line, message, hint }` diagnostics. Iterate against
the checker -- it catches typos in record field access and other static
issues without running the program. Then run `python3 cr8script.py file.cr8`.

## Canonical idioms

- Return a value from `if`: use the expression form `let x = if cond then a else b end`.
- "Does this list contain X?" -> `xs.contains(x)`, never a manual loop.
- "Sum a field across records" -> `sum(records | map field)`.
- Safe field access on a record literal of unknown shape -> `r.get("key")`.
- Safe HTTP success check -> `r.ok and r.status is at least 200 and r.status is less than 400`.
- Print formatted output -> `show f"{name}: {count}"`, not concatenation.

## See also

- [`AGENTS.md`](AGENTS.md) -- system-prompt template and tool definitions
  for using cr8script inside an LLM agent loop.
- [`LLM_MAP.md`](LLM_MAP.md) -- typed planning-graph format that sits
  between a prompt and the `.cr8` artifact, with its own JSON validator
  (`tools/check_map.py`) that mirrors `--check-json`.
- [`examples/agent_loop/`](examples/agent_loop/) -- the self-correction
  loop demo, end to end (broken -> diagnostics -> fixed).

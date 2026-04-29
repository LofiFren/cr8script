# LLM Map for cr8script

An LLM map is a **typed 2D planning graph** that sits between a user prompt and
the final `.cr8` program.

It is not a code file and not a purely decorative mind map. Its job is to make
the model externalize:

- what the task is trying to do
- what inputs exist
- what transformations are required
- what risks need guarding
- what output shape is expected
- what checks must pass

The map is the **planning artifact**. The `.cr8` file is the **execution
artifact**.

## Why this exists

cr8script already has a strong correction loop:

```text
prompt -> write .cr8 -> --check-json -> repair -> run
```

The LLM map adds a lighter-weight step before code generation:

```text
prompt -> map -> render / scaffold .cr8 -> --check-json -> repair -> run
```

That creates a shortcut path for LLMs:

- less blank-page generation
- clearer canonical task structure
- easier repair because failures can be traced back to a map node
- better benchmarking because the plan itself is inspectable

## Representation

The source of truth should be **2D logical structure**, not spatial semantics.

Recommended storage:

- JSON file
- flat node list
- explicit edge list
- small typed metadata objects

Humans can view that graph as:

- 2D mind map
- 2D flow chart
- optional 3D viewer

But 3D should be presentation only, never the canonical form.

## Node types

The v1 map uses these node kinds:

- `goal`
- `input`
- `transform`
- `decision`
- `risk`
- `output`
- `check`
- `artifact`

### `goal`

High-level intent of the task.

Example:

```json
{ "id": "goal_game", "kind": "goal", "label": "Build a browser-playable balloon game" }
```

### `input`

Named incoming values, constraints, or external facts.

Examples:

- player controls
- starting lives
- fuel capacity
- browser canvas size

### `transform`

A deterministic operation or subsystem.

Examples:

- update physics
- spawn collectibles
- resolve collisions
- render frame

### `decision`

A branch or rule that controls behavior.

Examples:

- burn only when fuel > 0
- if lives <= 0 then game over
- if collision type is ring then score

### `risk`

Known failure modes or ambiguity traps.

Examples:

- fuel underflow
- collision bug
- restart not restoring state

### `output`

User-visible results or program outputs.

Examples:

- generated HTML
- score HUD
- game-over overlay

### `check`

Acceptance criteria or invariants.

Examples:

- checker passes
- ring collision increases score
- storm collision reduces lives
- reset restores start state

### `artifact`

Concrete files or generated outputs.

Examples:

- `.cr8` generator
- generated `.html`
- browser screenshot

## Edge types

Edges are explicit and typed. The kind is the relationship -- what
*this connection* says about how the source affects the target.

| kind       | typical source kinds          | typical target kinds      | meaning |
|------------|-------------------------------|---------------------------|---------|
| `feeds`    | input / transform             | transform / decision      | data flows from source into target |
| `uses`     | transform                     | input / artifact          | source consumes a static resource |
| `guards`   | decision                      | transform / output        | target only happens when source allows |
| `produces` | transform / artifact          | output / artifact         | source generates target |
| `verifies` | check / debug-transform / risk| check / artifact          | source asserts something about target (or vice versa) |
| `risks`    | risk                          | transform / output        | source could break target |

`feeds` is the catch-all for data-flow edges. The other five carry real
semantic information that a checker or rendering tool can act on.

Examples:

- `input -> transform` with `feeds`
- `decision -> transform` with `guards`
- `transform -> output` with `produces`
- `risk -> check` with `verifies`

## Minimal schema

Each map should contain:

```json
{
  "title": "Skybound Ember",
  "task_kind": "html_game",
  "summary": "Generate a browser-playable hot air balloon game from a cr8script build script.",
  "nodes": [],
  "edges": []
}
```

Each node:

```json
{
  "id": "physics",
  "kind": "transform",
  "label": "Update balloon physics",
  "details": [
    "apply gravity",
    "apply burner thrust",
    "clamp within play area"
  ],
  "tags": ["gameplay", "runtime"]
}
```

Each edge:

```json
{
  "from": "controls",
  "to": "physics",
  "kind": "feeds",
  "label": "steer and climb input"
}
```

## v1 authoring rules

Keep the map compact:

- 1 goal node
- 2-6 input nodes
- 3-8 transform/decision nodes
- 1-4 risk nodes
- 1-3 output nodes
- 2-6 check nodes

Do not turn the map into hidden chain-of-thought. It should be:

- inspectable
- reproducible
- safe to store
- useful to both humans and tools

## How it should be used

### For models

The model should:

1. classify the task
2. build the map
3. choose canonical cr8script idioms per node
4. generate the `.cr8` artifact
5. repair via `--check-json`

### For humans

The human should be able to ask:

- is the plan complete?
- are the outputs clear?
- do the checks match the goal?
- are the main risks covered?

### For benchmarking

The map gives you an intermediate artifact to compare across languages:

- how many nodes were needed
- whether the plan was structurally correct
- whether repair changed code only or also the plan

## Validation: the `--check-json` analogue

The map gets the same self-correction loop the language gets. A model
generates the JSON, runs the checker, parses the issue list, fixes,
re-runs.

```bash
python3 tools/check_map.py path/to/file.map.json
python3 tools/check_map.py path/to/file.map.json --json   # JSON output for agents
```

The output shape mirrors `cr8script.py --check-json` exactly:

```json
[
  { "severity": "error",
    "path": "nodes[2].kind",
    "message": "`blarg` is not a valid node kind",
    "hint": "valid: artifact, check, decision, goal, input, output, risk, transform" },
  { "severity": "warning",
    "path": "nodes[7].id",
    "message": "`check` node `check_reset` has no incoming edge",
    "hint": "checks should be the target of `verifies` from risks/artifacts" }
]
```

What the validator checks:

- **schema** -- required fields (`title`, `task_kind`, `summary`, `nodes`,
  `edges`); `kind` values inside the allowed sets; `id`/`label`/`from`/
  `to`/`kind` present on every node and edge
- **structure** -- node ids unique; every edge endpoint resolves to a
  real node; at least one `goal` (warning above one); `check`s have an
  incoming edge; `output`s have an incoming edge; `artifact`s aren't
  unconnected; nothing is orphaned

## Code drift: keeping the map honest

A map without a link to code can quietly go stale. The convention is a
single-line comment on or above the cr8 code that implements a node:

```cr8
# llmmap: transform_physics
to update_physics(state)
  ...
end
```

Then the validator can flag drift between map and code:

```bash
python3 tools/check_map.py \
  examples/llm_map/balloon_game.map.json \
  --drift examples/make_balloon_game.cr8
```

The drift check is two-way:

- structural map nodes (`transform`, `decision`, `artifact`, `check`)
  with no `# llmmap:` annotation in any scanned file -> warning
- `# llmmap: <id>` comments referencing an id missing from the map ->
  error

Goals, inputs, outputs, and risks don't need an annotation -- they're
organizational nodes without a direct code site.

## When is the map worth the tokens

The map is a tool, not a tax. Roughly:

| task shape | use a map? |
|------------|------------|
| one-shot data transform (parse, summarize, format) | **no** -- go straight to .cr8 |
| multi-stage pipeline with branching logic          | **yes** -- risks and decisions earn the nodes |
| code generator (HTML, SVG, game) with > ~100 lines | **yes** -- blank-page generation is the main cost |
| anything you'd benchmark across model versions     | **yes** -- the map is the apples-to-apples surface |
| an agent loop where humans review intermediate output | **yes** -- the map is what they review |

Rule of thumb: if you can describe the task in one sentence and the
output fits on one screen, skip the map. Past that, the map starts
paying for itself.

## Prototype files

This repo's first prototype lives at:

- [examples/llm_map/balloon_game.map.json](examples/llm_map/balloon_game.map.json) -- the map source of truth
- [examples/make_balloon_game.cr8](examples/make_balloon_game.cr8) -- the implementation referenced by the map
- [tools/render_llm_map.py](tools/render_llm_map.py) -- JSON -> self-contained HTML/SVG view
- [tools/check_map.py](tools/check_map.py) -- schema, structure, and drift checker

Render the map with:

```bash
python3 tools/render_llm_map.py \
  examples/llm_map/balloon_game.map.json \
  examples/llm_map/balloon_game.map.html
```

## Practical recommendation

Use the LLM map as a **pre-code structure layer**, not as a replacement for
code or for the checker.

The right stack is:

```text
LLM map -> scaffold / codegen -> --check-json -> run -> acceptance checks
```

That keeps open thought where it helps, and constrains execution where it
actually reduces cost.

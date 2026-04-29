# LLM map prototype

This folder contains the first `cr8script` LLM-map prototype:

- `balloon_game.map.json` -- typed planning graph for the hot air balloon game
- `balloon_game.map.html` -- rendered 2D view

Render the HTML view from the JSON source:

```bash
python3 tools/render_llm_map.py \
  examples/llm_map/balloon_game.map.json \
  examples/llm_map/balloon_game.map.html
```

Open the output in a browser.

The source of truth is the JSON map, not the HTML layout.

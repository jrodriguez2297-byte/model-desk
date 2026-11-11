# Model Desk

A private, local dashboard for people who use Claude Code and other AI models. It answers three
questions every morning, on your own machine:

1. **Which model should I use?** Benchmarks, human-vote scores and prices, side by side.
2. **What can run on my machine for free?** Open models from the Ollama library, sized against your memory.
3. **Where do my Claude Code tokens go?** A usage audit from your own session logs, with the changes that would save the most.

It also tracks GitHub repos you care about and finds fast-rising new ones.

No server, no account, no AI in the daily run. A timer runs a plain Python script at 6 AM and
writes one file: `~/.model-desk/dashboard.html`.

## Set up (about 10 minutes)

You need [uv](https://docs.astral.sh/uv/). Optional: the [gh CLI](https://cli.github.com) signed in
(GitHub section) and [Ollama](https://ollama.com) (shows which models you have installed).

1. Get a free API key at <https://artificialanalysis.ai/data-api>.
2. Save it. Either put `AA_API_KEY=...` in `~/.model-desk/.env`, or on macOS run
   `security add-generic-password -a "$USER" -s aa-api-key -w` and paste it when asked.
3. Build the dashboard once: `uv run refresh.py`
4. Open `~/.model-desk/dashboard.html` in your browser.
5. Make it daily: `uv run python install_timer.py` (remove with `--remove`).

## Your settings

Everything of yours lives in `~/.model-desk/` and never in this repo.

| File | What it holds |
|---|---|
| `config.json` | Time zone, memory size, usage audit privacy, your own model name fixes |
| `watchlist.json` | Repos you follow and the GitHub searches for new ones |
| `history/` | Star snapshots and daily usage totals, kept forever, with 7 daily backups in `backups/` |
| `findings/` | A dated note of what the GitHub check noticed each day |

## Privacy

- The usage audit reads `~/.claude/projects` on your machine. Only totals are written: no prompts, no file contents.
- Session titles and project names are **off** by default. Turn them on with `usage.show_titles` and `usage.show_projects`.
- `usage.exclude_paths` skips any session whose folder contains one of your strings.
- Set `usage.enabled` to `false` to skip the audit entirely.
- Nothing is uploaded anywhere. The script only downloads from the sources below.

## Sources and credit

| Source | Used for | Terms |
|---|---|---|
| [Artificial Analysis](https://artificialanalysis.ai) free Data API | Intelligence Index, price, speed | Your own key, your own use, attribution required. Do not republish the data. |
| [LMArena leaderboard dataset](https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset) | Human-vote scores, licenses | CC-BY-4.0 |
| [Ollama library](https://ollama.com/library) | Local builds and download sizes | Public pages |
| [OpenRouter models API](https://openrouter.ai/api/v1/models) | Hosted prices, Claude cache prices | Public API |
| GitHub API (through `gh`) | Repo stars and releases | Your own login |

## When something breaks

Each source is independent. If one fails, its section keeps yesterday's data and shows a note
saying which source failed and why. Details are in `~/.model-desk/refresh.log`.

## Tests

`uv run --with pytest --with pyarrow pytest`

## License

Apache-2.0. Includes [d3](https://d3js.org) (ISC license) in `page/vendor/`.

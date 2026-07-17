# gemma-coder

**Your AI agent plans and reviews. A local model writes the code. Zero API cost for every line generated.**

gemma-coder is an [Agent Skill](https://agentskills.io) — one folder that plugs into
**Claude Code**, **Google Antigravity (CLI & IDE)**, **OpenAI Codex CLI**, and any
other agent that supports the open `SKILL.md` standard. It flips the usual economics:
the frontier model does the thinking (architecture, task specs, code review, tests)
while a free local model — Gemma, Qwen, anything you can run — writes every line of
application code on your own machine.

---

## How it works

```
you ──"build X"──▶ agent (Claude / Gemini / GPT)
                     │  1. writes tasks/PLAN.md + per-file specs
                     │  2. runs scripts/gemma_worker.py per task ──▶ local model
                     │                                               (writes the file)
                     │  3. reviews the code, runs tests
                     │  4. on failure: improves the SPEC and re-delegates
                     ▼
                 working, reviewed code — written locally, for free
```

The strict rule the skill enforces: **the agent never writes application source files
itself.** It writes plans, specs, and tests; the local model writes the code. When
generated code fails, the agent fixes the *spec* and retries (up to 2×) rather than
silently rewriting — so the local model stays the author.

## Requirements

- Python 3.9+ (standard library only, no pip installs)
- **One** local model runtime — ollama is the easiest but NOT required:

| Runtime | Setup | API used |
|---|---|---|
| [ollama](https://ollama.com) | `ollama pull gemma4` | native (port 11434) |
| [LM Studio](https://lmstudio.ai) | enable its local server | OpenAI-compatible (1234) |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | `llama-server -m model.gguf` | OpenAI-compatible (8080) |
| [mlx_lm](https://github.com/ml-explore/mlx-lm) (Apple Silicon) | `mlx_lm.server --model <repo>` | OpenAI-compatible (8080) |

## Install (all agents at once)

```sh
git clone https://github.com/harshdattani23/gemma-vibecoding-skills
cd gemma-vibecoding-skills
./install.sh                # symlinks into every agent skill dir found on your machine
python3 scripts/setup.py    # detects your runtime, lists models, saves your pick
```

`setup.py` writes `~/.config/gemma-coder/config.json`. That single config is shared
by every agent — pick your model once, use it everywhere. Switch models anytime:

```sh
python3 scripts/setup.py --save gemma4:26b-nvfp4
```

---

## Per-agent setup & usage

### Claude Code

`install.sh` links the skill into `~/.claude/skills/gemma-coder` (all projects).
For a single project instead: copy this folder to `<project>/.claude/skills/gemma-coder`.

Verify: run `claude`, then ask *"what skills do you have?"* — `gemma-coder` should be listed.

Use it:
```
> use gemma-coder to build a CLI todo app in this directory
```
Claude will write the plan and specs, delegate each file to your local model, test the
results, and report which files passed and which needed spec retries.

### Google Antigravity (CLI `agy` + IDE)

`install.sh` links the skill into `~/.gemini/config/skills/gemma-coder` — the global
location read by the Antigravity IDE, CLI, and browser agent alike. Per-project
alternative: `<project>/.agents/skills/gemma-coder`.

Verify:
```sh
agy -p "List the agent skills you have available."
```

Use it interactively (you'll approve tool permissions as they appear):
```sh
agy -i "Use your gemma-coder skill to build a markdown-to-html converter in src/"
```

**Headless mode** (`agy -p`) cannot show permission prompts, so pre-approve the two
skill scripts once — add to `permissions.allow` in `~/.gemini/antigravity-cli/settings.json`:
```json
"command(python3 ~/.gemini/config/skills/gemma-coder/scripts/gemma_worker.py)",
"command(python3 ~/.gemini/config/skills/gemma-coder/scripts/setup.py)"
```
(Use the full expanded path if your Antigravity version doesn't expand `~`.)

### OpenAI Codex CLI

`install.sh` links the skill into `~/.agents/skills/gemma-coder` (Codex's global
skills dir, shared with the open standard). Per-project: `<project>/.codex/skills/gemma-coder`.

Verify: run `codex`, then `/skills` (or ask *"what skills do you have?"*).

Use it:
```
> use the gemma-coder skill to add a REST API to this project
```

### Any other agent

If it supports the Agent Skills standard, copy or symlink this folder into its skills
directory — nothing here is agent-specific. If it doesn't, you can still paste
`SKILL.md` into its custom-instructions file (`AGENTS.md`, rules, etc.); the scripts
are plain CLIs.

---

## Getting models

Models can come from the **ollama library** or from **Hugging Face** — both are fully
supported. Pick whichever option below fits your setup.

### Option A — ollama library (simplest)

```sh
ollama pull gemma4            # or gemma4:26b-nvfp4, qwen3.6:35b-a3b, ...
python3 scripts/setup.py      # pick it, done
```

### Option B — Hugging Face model, served by ollama

Any GGUF repo on the Hub works directly — no conversion, no waiting for an ollama
library release:

1. Find a GGUF build on [huggingface.co](https://huggingface.co/models?library=gguf)
   (for coding: `unsloth/...-GGUF` and `bartowski/...-GGUF` repos are reliable).
2. Pull it with the `hf.co/` prefix and a quant tag:
   ```sh
   ollama pull hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q3_K_XL
   ollama pull hf.co/unsloth/gemma-4-27b-it-GGUF:Q4_K_M
   ```
3. Re-run `python3 scripts/setup.py` — the HF model appears in the list like any
   other; pick it and it becomes your coder.

### Option C — Hugging Face model, no ollama at all

- **LM Studio**: use its built-in search (it downloads from the Hub), load the model,
  enable the local server → run `setup.py`, it's detected on port 1234.
- **llama.cpp**: `llama-server -hf unsloth/gemma-4-27b-it-GGUF:Q4_K_M` downloads from
  the Hub and serves it → detected on port 8080.
- **mlx_lm** (Apple Silicon): `mlx_lm.server --model mlx-community/gemma-4-27b-it-4bit`
  pulls an MLX build from the Hub → detected on port 8080.

### Option D — Hugging Face hosted inference (not local, not free)

If your machine can't run a good model, the worker also speaks to any hosted
OpenAI-compatible endpoint, including HF Inference Providers. Set an API key and
point the config at the router:

```sh
export GEMMA_CODER_API_KEY=hf_...     # or put "api_key" in the config file
python3 scripts/setup.py --save "Qwen/Qwen3.6-35B-A3B" \
    --url https://router.huggingface.co --api openai
```

This trades "free and private" for "no hardware requirements" — your specs and
context files are sent to the provider.

**Picking the right file type:** choose **GGUF** builds for ollama / llama.cpp /
LM Studio, **MLX** builds for `mlx_lm`. Plain safetensors repos (e.g. NVFP4/TensorRT
builds) are for GPU serving stacks like vLLM and won't load in these runtimes.

**Model recommendations** (tested on a 24 GB Apple Silicon Mac):
- `gemma4:26b-nvfp4` — best quality/speed balance, ~45 tok/s, needs ~17 GB free
- `gemma4:12b-nvfp4` — light option (7.7 GB), leaves RAM for everything else
- `hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q3_K_XL` — MoE, 3B active params: fast AND strong
- Reasoning variants are handled automatically — the worker disables thinking so all
  tokens go to code (`think: false` on ollama; `<think>` blocks stripped elsewhere).

## Manual use (no agent at all)

The default link-mode installer exposes `gemma-coder` under `~/.local/bin`:

```sh
gemma-coder worker --task tasks/01-parser.md --out src/parser.py \
    --context src/types.py --expect 'class Parser' --stream
```

The worker rejects explicitly mismatched language fences, validates supported
languages before atomically replacing the output, and creates `<out>.bak` when
replacing an existing file. Use `--no-validate` or `--no-backup` only deliberately.

For multi-file work, use an explicit dependency manifest:

```json
{
  "tasks": [
    {"id": "types", "spec": "01-types.md", "output": "src/types.py"},
    {
      "id": "parser",
      "spec": "02-parser.md",
      "output": "src/parser.py",
      "depends_on": ["types"]
    }
  ]
}
```

```sh
gemma-coder batch --manifest tasks/manifest.json --project-root . --retries 2
```

Specs are relative to the manifest directory; outputs are project-relative. The
batch runner validates dependencies, detects cycles, blocks dependents after a
failure, and feeds validation errors back into retry prompts. See `SKILL.md` for the
full workflow and exit-code contract.

## Configuration

`~/.config/gemma-coder/config.json` (override location with `$GEMMA_CODER_CONFIG`):
```json
{
  "model": "gemma4:26b-nvfp4",
  "base_url": "http://localhost:11434",
  "api": "ollama",
  "temperature": 0.2,
  "num_ctx": 16384
}
```

Optional: `"api_key"` (or env var `$GEMMA_CODER_API_KEY`) — sent as a Bearer token on
OpenAI-compatible endpoints; only needed for hosted providers, never for local servers.

## Troubleshooting

| Symptom | Fix |
|---|---|
| worker exits 2: "no backend configured" | run `python3 scripts/setup.py` |
| worker exits 2: "cannot reach ..." | start your model server (`ollama serve`, LM Studio, ...) |
| "no code block in response" | model too small / spec too vague — try a bigger model or tighter spec |
| empty responses from a reasoning model via OpenAI API | use ollama's native API for that model (`--api ollama`) |
| Antigravity headless auto-denies the worker | add the allow-rules shown above |
| model produces subtly wrong code | that's the design working: the agent's tests catch it and the spec gets improved |

## License

MIT

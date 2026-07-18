# gemma-coder

**Save your frontier tokens for thinking. A free local model writes every line of code — and tight specs make open models write it well.**

gemma-coder is an [Agent Skill](https://agentskills.io) — one folder that plugs into
**Claude Code**, **Google Antigravity (CLI & IDE)**, **OpenAI Codex CLI**, and any
other agent that supports the open `SKILL.md` standard. It's built on two ideas:

1. **Save tokens.** Most of what a coding agent burns tokens on is typing code
   out, not thinking. Here the frontier model spends tokens only on what it's
   uniquely good at — architecture, per-file specs, code review, tests — while a
   free local model (Gemma, Qwen, anything you can run) writes every line of
   application code on your own machine. Zero API cost per generated line.
2. **Boost open-model code quality.** Small models don't fail on well-specified
   files; they fail on vague prompts. The skill enforces a strict contract — the
   agent never writes application source itself, and when generated code fails
   tests it improves the *spec* and re-delegates — so a 12–27B local model
   reliably produces code it could never write from a one-line ask.

The only requirement besides an agent: **Python 3.9+** (standard library only) and
**one** local model runtime (step 2 below — ollama is the easiest).

---

## Step 1 — Install the skill

**Quickest: npx (no clone)**

```sh
npx skills add vibecoding-skills/gemma-vibecoding-skills
```

Installs the skill into your agents' skill directories via the
[skills CLI](https://skills.sh). Update later with `npx skills update`
(npx installs are snapshot copies).

**Or from a clone** (adds the `gemma-coder` command to your PATH):

```sh
git clone https://github.com/vibecoding-skills/gemma-vibecoding-skills
cd gemma-vibecoding-skills
./install.sh                # symlinks into every agent skill dir found on your machine
```

`install.sh` puts a `gemma-coder` command on `~/.local/bin` (make sure that's on
your `PATH`). Everything is symlinked to the checkout, so a plain `git pull`
updates every agent at once — no reinstall needed. Run `./install.sh --copy`
instead for a standalone copy that survives deleting the clone (update it by
pulling and re-running `./install.sh --copy`).

## Step 2 — Set up local Gemma

1. **Install ollama** — download from [ollama.com/download](https://ollama.com/download)
   (macOS/Windows installer or `curl -fsSL https://ollama.com/install.sh | sh` on
   Linux). The app runs the server automatically; otherwise start it with
   `ollama serve`.

2. **Pull a Gemma model** sized to your RAM:

   ```sh
   ollama pull gemma4:12b-nvfp4   # 7.7 GB — fits comfortably on 16 GB machines
   ollama pull gemma4:26b-nvfp4   # ~17 GB free RAM — best quality/speed balance
   ```

   (Full device guide: [Recommended Gemma by device](#recommended-gemma-by-device).)

3. **Point the skill at it:**

   ```sh
   gemma-coder setup            # clone install
   # npx install (no command on PATH):
   python3 ~/.claude/skills/gemma-coder/scripts/setup.py
   ```

   Setup detects the running server, lists every pulled model, and saves your pick
   to `~/.config/gemma-coder/config.json` — one config shared by every agent.
   Switch models anytime with `gemma-coder setup --save <model>`.

4. **Verify with a one-file generation:**

   ```sh
   printf 'Create hello.py that prints "hello from gemma"\n' > /tmp/task.md
   gemma-coder worker --task /tmp/task.md --out /tmp/hello.py
   python3 /tmp/hello.py
   ```

Not an ollama user? LM Studio, llama.cpp `llama-server`, and `mlx_lm.server` all
work the same way — start their local server, run setup, pick the model:

| Runtime | Setup | API used |
|---|---|---|
| [ollama](https://ollama.com) | `ollama pull gemma4` | native (port 11434) |
| [LM Studio](https://lmstudio.ai) | enable its local server | OpenAI-compatible (1234) |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | `llama-server -m model.gguf` | OpenAI-compatible (8080) |
| [mlx_lm](https://github.com/ml-explore/mlx-lm) (Apple Silicon) | `mlx_lm.server --model <repo>` | OpenAI-compatible (8080) |

## Step 3 — Use it from your agent

```
> use gemma-coder to build a CLI todo app in this directory
```

The agent writes the plan and specs, delegates each file to your local model,
tests the results, and reports what passed. Per-agent specifics are below.

---

## How it works

```
you ──"build X"──▶ agent (Claude / Gemini / GPT)
                     │  1. writes per-file specs (+ tasks/manifest.json for multi-file)
                     │  2. runs gemma-coder worker (or batch) ──▶ local model
                     │                                            (writes the file)
                     │  3. reviews the code, runs tests
                     │  4. on failure: improves the SPEC and re-delegates
                     ▼
                 working, reviewed code — written locally, for free
```

The strict rule the skill enforces: **the agent never writes application source files
itself.** It writes plans, specs, and tests; the local model writes the code. When
generated code fails, the agent fixes the *spec* and retries (up to 2×) rather than
silently rewriting — so the local model stays the author, and each retry tightens
the spec, which is exactly what small open models need to produce good code.

---

## Per-agent setup & usage

### Claude Code

`install.sh` links the skill into `~/.claude/skills/gemma-coder` (all projects).
For a single project instead: copy the `gemma-coder/` folder to `<project>/.claude/skills/gemma-coder`.

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
"command(python3 ~/.gemini/config/skills/gemma-coder/scripts/gemma_batch.py)",
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

If it supports the Agent Skills standard, copy or symlink the `gemma-coder/` folder
into its skills directory — nothing here is agent-specific. If it doesn't, you can still paste
`SKILL.md` into its custom-instructions file (`AGENTS.md`, rules, etc.); the scripts
are plain CLIs.

---

## Getting models

Models can come from the **ollama library** or from **Hugging Face** — both are fully
supported. Pick whichever option below fits your setup.

### Option A — ollama library (simplest)

```sh
ollama pull gemma4            # or gemma4:12b-nvfp4, gemma4:26b-nvfp4, gemma4:31b, ...
gemma-coder setup      # pick it, done
```

### Option B — Hugging Face model, served by ollama

Any GGUF repo on the Hub works directly — no conversion, no waiting for an ollama
library release:

1. Find a GGUF build on [huggingface.co](https://huggingface.co/models?library=gguf)
   (for coding: `unsloth/...-GGUF` and `bartowski/...-GGUF` repos are reliable).
2. Pull it with the `hf.co/` prefix and a quant tag:
   ```sh
   ollama pull hf.co/unsloth/gemma-4-27b-it-GGUF:Q4_K_M
   ollama pull hf.co/unsloth/gemma-4-27b-it-GGUF:Q8_0
   ```
3. Re-run `gemma-coder setup` — the HF model appears in the list like any
   other; pick it and it becomes your coder.

### Option C — Hugging Face model, no ollama at all

- **LM Studio**: use its built-in search (it downloads from the Hub), load the model,
  enable the local server → run `gemma-coder setup`, it is detected on port 1234.
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
gemma-coder setup --save "google/gemma-4-27b-it" \
    --url https://router.huggingface.co --api openai
```

This trades "free and private" for "no hardware requirements" — your specs and
context files are sent to the provider.

**Picking the right file type:** choose **GGUF** builds for ollama / llama.cpp /
LM Studio, **MLX** builds for `mlx_lm`. Plain safetensors repos (e.g. NVFP4/TensorRT
builds) are for GPU serving stacks like vLLM and won't load in these runtimes.

## Recommended Gemma by device

One family, every device — pick by the memory you can spare while your agent and
editor are also running:

| Your device | Pull this | Size | What to expect |
|---|---|---|---|
| 8 GB RAM laptop, CPU-only | `gemma4:e2b-it-qat` | 4.3 GB | Boilerplate, configs, small single-purpose files |
| 16 GB Apple Silicon / 8 GB VRAM GPU | `gemma4:12b-nvfp4` | 7.7 GB | Solid daily driver; leaves RAM for the rest of your stack |
| 24 GB Apple Silicon / 16 GB VRAM GPU | `gemma4:26b-nvfp4` | ~17 GB | The sweet spot — ~45 tok/s, reliably writes 500+ line files (this repo's own scripts are written by it) |
| 32 GB+ RAM / 24 GB VRAM GPU | `gemma4:31b` | 20 GB | Strongest local Gemma; best for intricate core logic |

Rules of thumb:

- **Long files need bigger models.** Small models tend to stop generating around a
  couple thousand tokens, which truncates files beyond ~200 lines — the worker
  rejects the truncated output (exit 1), but you'll waste retries. Use 12b+ for
  anything substantial, 26b for long files.
- **Split the work by task**: `--model` overrides the default per call, so a fast
  small model can handle trivial files while 26b writes the core logic.
- **RAM headroom matters**: the sizes above are model downloads; leave a few GB
  free beyond them or generation slows to a crawl.
- Reasoning variants are handled automatically — the worker disables thinking so
  all tokens go to code (`think: false` on ollama; `<think>` blocks stripped
  elsewhere).

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
| worker exits 2: "no backend configured" | run `gemma-coder setup` |
| worker exits 2: "request failed: ..." | start your model server (`ollama serve`, LM Studio, ...) |
| worker exits 1: "no acceptable code block" | model too small / spec too vague — the raw response is printed to stderr; try a bigger model or tighter spec |
| worker exits 3: "validation failed" | generated code didn't parse (or missed `--expect`) — the old file is untouched; improve the spec and retry |
| worker exits 4 after Ctrl-C during `--stream` | expected: partial output is in `<out>.partial`, the target file is untouched |
| a `.bak` file appeared next to the output | expected: the previous version is kept when a file is replaced (disable with `--no-backup`) |
| generated file with its own code fences comes out truncated | extraction honors fence length — this only happens when the model wraps a markdown file in a same-length fence; re-run, or tell the spec to use a four-backtick outer fence |
| empty responses from a reasoning model via OpenAI API | use ollama's native API for that model (`--api ollama`) |
| Antigravity headless auto-denies the worker | add the allow-rules shown above |
| model produces subtly wrong code | that's the design working: the agent's tests catch it and the spec gets improved |

## License

MIT

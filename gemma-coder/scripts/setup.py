#!/usr/bin/env python3
"""gemma-coder setup: detect local model backends, pick a model, save config.

Usage:
    python3 setup.py            # interactive: detect, pick model, save
    python3 setup.py --list     # show detected backends and their models
    python3 setup.py --save MODEL [--url URL --api ollama|openai]

No ollama? Any OpenAI-compatible local server works too (LM Studio, llama.cpp
llama-server, mlx_lm.server). This script probes the usual local ports.
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.request

CONFIG_PATH = pathlib.Path(os.environ.get(
    "GEMMA_CODER_CONFIG",
    str(pathlib.Path.home() / ".config" / "gemma-coder" / "config.json")))

# (name, base_url, api_type)
KNOWN_BACKENDS = [
    ("ollama", "http://localhost:11434", "ollama"),
    ("lmstudio", "http://localhost:1234", "openai"),
    ("llama.cpp / mlx_lm / other", "http://localhost:8080", "openai"),
]

INSTALL_HINTS = """No local model server found. Options (any ONE of these):
  * ollama (easiest):      https://ollama.com/download   then e.g.: ollama pull gemma4
  * LM Studio (GUI):       https://lmstudio.ai   (enable the local server, port 1234)
  * llama.cpp:             brew install llama.cpp   then: llama-server -m model.gguf
  * mlx_lm (Apple):        pip install mlx-lm   then: mlx_lm.server --model <hf-repo>
Then re-run this script."""


def get_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def list_models(base_url, api):
    try:
        if api == "ollama":
            data = get_json(base_url + "/api/tags")
            return [m["name"] for m in data.get("models", [])]
        data = get_json(base_url + "/v1/models")
        return [m["id"] for m in data.get("data", [])]
    except Exception:
        return None


def detect():
    """Return list of (name, base_url, api, models) for live backends."""
    found = []
    for name, url, api in KNOWN_BACKENDS:
        models = list_models(url, api)
        if models is not None:
            found.append((name, url, api, models))
    return found


def save_config(model, base_url, api):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = {"model": model, "base_url": base_url, "api": api,
           "temperature": 0.2, "num_ctx": 16384}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    print("Saved %s -> %s" % (model, CONFIG_PATH))


def cmd_list():
    found = detect()
    if not found:
        print(INSTALL_HINTS)
        return 1
    for name, url, api, models in found:
        print("%s (%s, %s API):" % (name, url, api))
        for m in models:
            print("  - %s" % m)
        if not models:
            print("  (no models installed)")
    return 0


def cmd_interactive():
    found = detect()
    if not found:
        print(INSTALL_HINTS)
        return 1
    options = [(name, url, api, m) for name, url, api, models in found for m in models]
    if not options:
        print("Backend found, but no models installed. e.g.: ollama pull gemma4")
        return 1
    print("Available local models:")
    for i, (name, _url, _api, m) in enumerate(options, 1):
        print("  %d) %s  [%s]" % (i, m, name))
    choice = input("Pick a model [1-%d]: " % len(options)).strip()
    try:
        name, url, api, model = options[int(choice) - 1]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return 1
    save_config(model, url, api)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show detected backends/models")
    ap.add_argument("--save", metavar="MODEL", help="save MODEL as the configured model")
    ap.add_argument("--url", help="backend base URL (with --save; default: auto-detect)")
    ap.add_argument("--api", choices=["ollama", "openai"], help="backend API type (with --save)")
    args = ap.parse_args()

    if args.list:
        return cmd_list()
    if args.save:
        url, api = args.url, args.api
        if not (url and api):
            for name, burl, bapi, models in detect():
                if args.save in models:
                    url, api = burl, bapi
                    break
            else:
                print("Model %r not found on any detected backend. "
                      "Pass --url and --api explicitly." % args.save)
                return 1
        save_config(args.save, url, api)
        return 0
    return cmd_interactive()


if __name__ == "__main__":
    sys.exit(main())

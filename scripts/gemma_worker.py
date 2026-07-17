#!/usr/bin/env python3
"""Send a one-file task spec to a model and safely write the generated code.

Exit codes:
    0  code written successfully
    1  model response did not contain acceptable code
    2  configuration, argument, backend, or network error
    3  generated code failed validation
    4  streaming interrupted; partial response saved separately
"""
import argparse
import dataclasses
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

CONFIG_PATH = pathlib.Path(os.environ.get(
    "GEMMA_CODER_CONFIG",
    str(pathlib.Path.home() / ".config" / "gemma-coder" / "config.json")))

SYSTEM_PROMPT = """You are a senior software engineer. You will receive a spec for exactly one file.
Respond with a single fenced code block containing the COMPLETE contents of that file.
No explanations before or after the code block. No placeholders or TODOs — write the full implementation.
Follow the spec exactly: file purpose, function signatures, and behavior are all requirements."""

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".sh": "bash",
    ".bash": "bash",
}

LANGUAGE_ALIASES = {
    "python": {"python", "py"},
    "javascript": {"javascript", "js", "node"},
    "bash": {"bash", "sh", "shell"},
}


@dataclasses.dataclass(frozen=True)
class ValidationResult:
    status: str
    message: str = ""


class BackendError(Exception):
    """A provider reported a permanent backend/protocol failure."""


class StreamInterrupted(Exception):
    """Streaming was interrupted; partial contains only already-received content."""

    def __init__(self, partial):
        super().__init__("stream interrupted")
        self.partial = partial


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def build_prompt(task_path, context_paths, out_path):
    parts = ["Target file to write: `%s`\n" % out_path]
    for ctx in context_paths:
        parts.append("Existing file `%s` (read-only context, do not rewrite it):\n"
                     "```\n%s\n```\n" % (ctx, ctx.read_text()))
    parts.append("## Task spec\n\n" + task_path.read_text())
    return "\n".join(parts)


def post_json(url, payload, timeout, api_key=None):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def report_ollama_stats(data):
    """Print Ollama generation statistics when timing metadata is available."""
    eval_count = data.get("eval_count", 0)
    eval_ns = data.get("eval_duration", 0)
    if eval_ns:
        print("[gemma-coder] %d tokens, %.1f tok/s, %.1fs total"
              % (eval_count, eval_count / (eval_ns / 1e9),
                 data.get("total_duration", 0) / 1e9), file=sys.stderr)


def call_ollama(base_url, model, prompt, options, timeout):
    data = post_json(base_url.rstrip("/") + "/api/chat", {
        "model": model,
        "stream": False,
        "think": False,
        "options": options,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }, timeout)
    report_ollama_stats(data)
    return data["message"]["content"]


def call_openai(base_url, model, prompt, options, timeout, api_key=None):
    data = post_json(base_url.rstrip("/") + "/v1/chat/completions", {
        "model": model,
        "temperature": options.get("temperature", 0.2),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }, timeout, api_key)
    return data["choices"][0]["message"]["content"]


def iter_ollama_ndjson(lines):
    """Yield decoded events from Ollama's newline-delimited JSON stream."""
    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.strip()
        if line:
            yield json.loads(line)


def iter_openai_sse(lines):
    """Yield decoded OpenAI-compatible SSE events with proper event framing."""
    data_lines = []
    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                if payload == "[DONE]":
                    return
                yield json.loads(payload)
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            if value == "[DONE]" and not data_lines:
                return
            data_lines.append(value)
    if data_lines:
        payload = "\n".join(data_lines)
        if payload != "[DONE]":
            yield json.loads(payload)


def ollama_event_content(event):
    error = event.get("error")
    if error:
        raise BackendError(str(error))
    message = event.get("message")
    return message.get("content", "") if isinstance(message, dict) else ""


def openai_event_content(event):
    error = event.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else error
        raise BackendError(str(message))
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    return delta.get("content", "") if isinstance(delta, dict) else ""


def collect_stream(events, content_from_event):
    """Collect and display stream chunks, retaining partial data on SIGINT."""
    chunks = []
    final_event = {}
    try:
        for event in events:
            final_event = event
            content = content_from_event(event)
            if content:
                chunks.append(content)
                print(content, end="", file=sys.stderr, flush=True)
            if event.get("done"):
                break
    except KeyboardInterrupt as exc:
        raise StreamInterrupted("".join(chunks)) from exc
    if chunks:
        print(file=sys.stderr)
    return "".join(chunks), final_event


def _stream_request(url, payload, timeout, parser, content_from_event, api_key=None):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return collect_stream(parser(response), content_from_event)


def call_ollama_stream(base_url, model, prompt, options, timeout):
    result = _stream_request(
        base_url.rstrip("/") + "/api/chat",
        {
            "model": model,
            "stream": True,
            "think": False,
            "options": options,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout,
        iter_ollama_ndjson,
        ollama_event_content,
    )
    report_ollama_stats(result[1])
    return result


def call_openai_stream(base_url, model, prompt, options, timeout, api_key):
    return _stream_request(
        base_url.rstrip("/") + "/v1/chat/completions",
        {
            "model": model,
            "temperature": options.get("temperature", 0.2),
            "stream": True,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout,
        iter_openai_sse,
        openai_event_content,
        api_key=api_key,
    )


def save_partial_response(response, output):
    """Save interrupted output separately without touching the requested file."""
    output = pathlib.Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    partial.write_text(response)
    return partial


def strip_thinking(text):
    """Remove common reasoning blocks before extracting fenced code."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<thinking>.*?</thinking>", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def detect_language(out_path, explicit=None):
    if explicit:
        return explicit.lower()
    return LANGUAGE_BY_SUFFIX.get(pathlib.Path(out_path).suffix.lower())


def extract_code(text, language=None):
    """Extract code, rejecting explicitly mismatched language fences."""
    blocks = re.findall(
        r"```([a-zA-Z0-9_+-]*)[ \t]*\r?\n(.*?)```", text, flags=re.DOTALL)
    if not blocks:
        return None
    if not language:
        return max((code for _tag, code in blocks), key=len)

    aliases = LANGUAGE_ALIASES.get(language, {language})
    matching = [code for tag, code in blocks if tag.lower() in aliases]
    if matching:
        return max(matching, key=len)
    untagged = [code for tag, code in blocks if not tag]
    if untagged:
        return max(untagged, key=len)
    return None


def compile_expect_pattern(pattern):
    if pattern is None:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError("invalid --expect regex: %s" % exc) from exc


def _validate_with_file(code, suffix, command):
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as handle:
            handle.write(code)
            path = handle.name
        proc = subprocess.run(command(path), capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return ValidationResult("skipped", "validator executable is not installed")
    except subprocess.TimeoutExpired:
        return ValidationResult("skipped", "validator timed out")
    finally:
        if path:
            pathlib.Path(path).unlink(missing_ok=True)
    if proc.returncode:
        return ValidationResult(
            "failed", proc.stderr.strip() or proc.stdout.strip() or "validator failed")
    return ValidationResult("passed")


def validate_code(code, language, filename="generated"):
    """Validate supported languages without implicit downloads or installs."""
    if language == "python":
        try:
            compile(code, filename, "exec")
        except SyntaxError as exc:
            return ValidationResult("failed", "%s: %s" % (exc.msg, exc.text or ""))
        return ValidationResult("passed")
    if language == "javascript":
        suffix = pathlib.Path(filename).suffix.lower()
        if suffix not in {".js", ".mjs", ".cjs"}:
            suffix = ".js"
        return _validate_with_file(code, suffix, lambda path: ["node", "--check", path])
    if language == "bash":
        return _validate_with_file(code, ".sh", lambda path: ["bash", "-n", path])
    return ValidationResult("skipped", "no validator configured for %s" % language)


def validate_and_write(code, output, language, expect_pattern, make_backup=True,
                       validate=True):
    """Validate first, then atomically replace output and optionally back it up."""
    output = pathlib.Path(output)
    if expect_pattern is not None and not expect_pattern.search(code):
        return ValidationResult("failed", "expected pattern was not found")

    result = validate_code(code, language, str(output)) if validate and language else \
        ValidationResult("skipped", "validation disabled or language unknown")
    if result.status == "failed":
        return result

    output.parent.mkdir(parents=True, exist_ok=True)
    backup = output.with_suffix(output.suffix + ".bak")
    if make_backup and backup.is_symlink():
        raise ValueError("backup path must not be a symlink: %s" % backup)
    if make_backup and backup.exists() and not stat.S_ISREG(backup.lstat().st_mode):
        raise ValueError("backup path must be a regular file: %s" % backup)

    if output.is_symlink():
        raise ValueError("output path must not be a symlink: %s" % output)
    if output.exists() and not stat.S_ISREG(output.lstat().st_mode):
        raise ValueError("output path must be a regular file: %s" % output)
    existing_mode = stat.S_IMODE(output.stat().st_mode) if output.exists() else None
    temp_path = None
    backup_temp = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", dir=str(output.parent), prefix=".%s." % output.name,
                suffix=".tmp", delete=False) as handle:
            handle.write(code)
            temp_path = pathlib.Path(handle.name)
        if existing_mode is None:
            current_umask = os.umask(0)
            os.umask(current_umask)
            os.chmod(temp_path, 0o666 & ~current_umask)
        else:
            os.chmod(temp_path, existing_mode)

        if make_backup and output.exists():
            with tempfile.NamedTemporaryFile(
                    dir=str(output.parent), prefix=".%s." % backup.name,
                    suffix=".tmp", delete=False) as handle:
                backup_temp = pathlib.Path(handle.name)
            shutil.copy2(output, backup_temp)
            os.replace(str(backup_temp), str(backup))
            backup_temp = None
        os.replace(str(temp_path), str(output))
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if backup_temp is not None:
            backup_temp.unlink(missing_ok=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="markdown file with the task spec")
    ap.add_argument("--out", required=True, help="path of the file to write")
    ap.add_argument("--context", nargs="*", default=[],
                    help="existing files to include as read-only context")
    ap.add_argument("--model", help="override configured model")
    ap.add_argument("--url", help="override backend base URL")
    ap.add_argument("--api", choices=["ollama", "openai"], help="override backend API type")
    ap.add_argument("--stream", action="store_true",
                    help="print generated content to stderr as it arrives")
    ap.add_argument("--lang", choices=sorted(LANGUAGE_ALIASES),
                    help="validation language (auto-detected from --out)")
    ap.add_argument("--expect", metavar="PATTERN",
                    help="regex that generated code must contain")
    ap.add_argument("--no-validate", action="store_true",
                    help="write without syntax validation")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not create <out>.bak before replacing an existing file")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    try:
        expect_pattern = compile_expect_pattern(args.expect)
    except ValueError as exc:
        print("[gemma-coder] %s" % exc, file=sys.stderr)
        return 2

    try:
        cfg = load_config()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print("[gemma-coder] invalid configuration %s: %s" % (CONFIG_PATH, exc),
              file=sys.stderr)
        return 2
    model = args.model or cfg.get("model")
    base_url = args.url or cfg.get("base_url")
    api = args.api or cfg.get("api")
    if not (model and base_url and api):
        print("[gemma-coder] no backend configured. Run: python3 %s"
              % (pathlib.Path(__file__).parent / "setup.py"), file=sys.stderr)
        return 2

    language = detect_language(args.out, args.lang)
    options = {"temperature": cfg.get("temperature", 0.2),
               "num_ctx": cfg.get("num_ctx", 16384)}
    try:
        prompt = build_prompt(pathlib.Path(args.task),
                              [pathlib.Path(p) for p in args.context], args.out)
        api_key = os.environ.get("GEMMA_CODER_API_KEY") or cfg.get("api_key")
        if args.stream and api == "ollama":
            response, _meta = call_ollama_stream(
                base_url, model, prompt, options, args.timeout)
        elif args.stream:
            response, _meta = call_openai_stream(
                base_url, model, prompt, options, args.timeout, api_key)
        elif api == "ollama":
            response = call_ollama(base_url, model, prompt, options, args.timeout)
        else:
            response = call_openai(base_url, model, prompt, options, args.timeout, api_key)
    except StreamInterrupted as exc:
        partial = save_partial_response(exc.partial, pathlib.Path(args.out))
        print("[gemma-coder] interrupted; partial response saved to %s" % partial,
              file=sys.stderr)
        return 4
    except (BackendError, OSError, TimeoutError, ValueError, json.JSONDecodeError,
            KeyError, urllib.error.URLError) as exc:
        print("[gemma-coder] request failed: %s" % exc, file=sys.stderr)
        return 2

    code = extract_code(strip_thinking(response), language)
    if code is None:
        print("[gemma-coder] ERROR: no acceptable code block in response", file=sys.stderr)
        preview = response[:2000]
        print("[gemma-coder] raw response:\n%s%s"
              % (preview, "\n[truncated]" if len(response) > len(preview) else ""),
              file=sys.stderr)
        return 1

    try:
        result = validate_and_write(
            code, pathlib.Path(args.out), language, expect_pattern,
            make_backup=not args.no_backup, validate=not args.no_validate)
    except (OSError, ValueError) as exc:
        print("[gemma-coder] output failed: %s" % exc, file=sys.stderr)
        return 2
    if result.status == "failed":
        print("[gemma-coder] validation failed: %s" % result.message, file=sys.stderr)
        return 3
    print("[gemma-coder] wrote %s (%d lines); validation=%s%s"
          % (args.out, len(code.splitlines()), result.status,
             " (%s)" % result.message if result.message else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

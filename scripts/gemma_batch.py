#!/usr/bin/env python3
"""Run gemma-coder tasks from an explicit JSON dependency manifest.

Manifest paths are unambiguously scoped:
- ``spec`` is relative to the manifest's directory.
- ``output`` is relative to ``--project-root``.
- ``depends_on`` contains task IDs, not file paths.
"""
import argparse
import dataclasses
import json
import pathlib
import subprocess
import sys
import tempfile

WORKER = pathlib.Path(__file__).resolve().parent / "gemma_worker.py"
WORKER_SHUTDOWN_GRACE = 30


@dataclasses.dataclass(frozen=True)
class Task:
    id: str
    spec: pathlib.Path
    output: pathlib.Path
    depends_on: tuple = ()


@dataclasses.dataclass(frozen=True)
class AttemptResult:
    exit_code: int
    stderr: str = ""


@dataclasses.dataclass(frozen=True)
class TaskResult:
    status: str
    attempts: int
    message: str = ""


def _safe_relative_path(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty project-relative path" % field)
    path = pathlib.Path(value)
    if path == pathlib.Path(".") or path.is_absolute() or ".." in path.parts:
        raise ValueError("%s must be a non-empty project-relative path: %s"
                         % (field, value))
    return path


def _resolve_contained(root, relative, field, must_exist=False):
    root = pathlib.Path(root).resolve(strict=True)
    try:
        candidate = (root / relative).resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError("invalid %s path %s: %s" % (field, relative, exc)) from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("%s path escapes its root: %s" % (field, relative)) from exc
    return candidate


def parse_manifest(data):
    """Validate parsed manifest JSON and return Task objects."""
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError("manifest must contain a tasks list")
    tasks = []
    seen = set()
    seen_outputs = set()
    for raw in data["tasks"]:
        if not isinstance(raw, dict):
            raise ValueError("each task must be an object")
        task_id = raw.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("each task requires a non-empty string id")
        if task_id in seen:
            raise ValueError("duplicate task id: %s" % task_id)
        seen.add(task_id)
        try:
            spec = _safe_relative_path(raw["spec"], "spec")
            output = _safe_relative_path(raw["output"], "output")
        except KeyError as exc:
            raise ValueError("task %s is missing %s" % (task_id, exc.args[0])) from exc
        if output in seen_outputs:
            raise ValueError("duplicate output path: %s" % output)
        seen_outputs.add(output)
        dependencies = raw.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(
                isinstance(item, str) for item in dependencies):
            raise ValueError("depends_on for %s must be a list of task IDs" % task_id)
        tasks.append(Task(task_id, spec, output, tuple(dependencies)))
    return tasks


def topological_order(tasks):
    """Return stable dependency order, rejecting missing dependencies and cycles."""
    by_id = {task.id: task for task in tasks}
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in by_id:
                raise ValueError("unknown dependency %s for task %s" % (dependency, task.id))

    pending = list(tasks)
    resolved = set()
    ordered = []
    while pending:
        ready = [task for task in pending if set(task.depends_on) <= resolved]
        if not ready:
            raise ValueError("dependency cycle involving: %s"
                             % ", ".join(task.id for task in pending))
        for task in ready:
            pending.remove(task)
            ordered.append(task)
            resolved.add(task.id)
    return ordered


def execute_batch(tasks, tasks_dir, project_root, runner, retries=2):
    """Execute tasks in dependency order with blocking and feedback-aware retries."""
    tasks_dir = pathlib.Path(tasks_dir).resolve(strict=True)
    project_root = pathlib.Path(project_root).resolve(strict=True)
    ordered = topological_order(tasks)
    by_id = {task.id: task for task in tasks}
    results = {}

    for task in ordered:
        blocked_by = [dependency for dependency in task.depends_on
                      if results[dependency].status != "passed"]
        if blocked_by:
            results[task.id] = TaskResult(
                "blocked", 0, "blocked by: %s" % ", ".join(blocked_by))
            continue

        try:
            spec_path = _resolve_contained(
                tasks_dir, task.spec, "spec", must_exist=True)
            output = _resolve_contained(
                project_root, task.output, "output", must_exist=False)
            context = [_resolve_contained(
                project_root, by_id[dependency].output,
                "dependency output", must_exist=True)
                       for dependency in task.depends_on]
        except ValueError as exc:
            results[task.id] = TaskResult("failed", 0, str(exc))
            continue
        if not spec_path.is_file():
            results[task.id] = TaskResult("failed", 0, "missing spec: %s" % spec_path)
            continue

        feedback = None
        last = AttemptResult(1, "not attempted")
        attempts = 0
        for _attempt in range(retries + 1):
            attempts += 1
            last = runner(task, spec_path, output, context, feedback)
            if last.exit_code == 0:
                if output.is_file():
                    results[task.id] = TaskResult("passed", attempts)
                else:
                    results[task.id] = TaskResult(
                        "failed", attempts,
                        "worker reported success but did not create: %s" % output)
                break
            if last.exit_code not in (1, 3):
                results[task.id] = TaskResult("failed", attempts, last.stderr)
                break
            feedback = last.stderr or "previous generation attempt failed"
        else:
            results[task.id] = TaskResult("failed", attempts, last.stderr)

    return results


def make_subprocess_runner(worker_args, timeout):
    """Create a runner that invokes gemma_worker.py and injects retry feedback."""
    def run(_task, spec_path, output, context, feedback):
        retry_spec = None
        selected_spec = spec_path
        try:
            if feedback:
                with tempfile.NamedTemporaryFile(
                        "w", suffix=".md", prefix="gemma-retry-", delete=False) as handle:
                    handle.write(spec_path.read_text())
                    handle.write("\n\n## Previous attempt failed\n\n```text\n")
                    handle.write(feedback)
                    handle.write("\n```\nCorrect the implementation so this failure does not recur.\n")
                    retry_spec = pathlib.Path(handle.name)
                    selected_spec = retry_spec
            command = [sys.executable, str(WORKER), "--task", str(selected_spec),
                       "--out", str(output), "--timeout", str(timeout)] + list(worker_args)
            if context:
                command += ["--context"] + [str(path) for path in context]
            try:
                proc = subprocess.run(
                    command, capture_output=True, text=True,
                    timeout=timeout + WORKER_SHUTDOWN_GRACE)
                return AttemptResult(proc.returncode, proc.stderr)
            except subprocess.TimeoutExpired as exc:
                return AttemptResult(
                    2, "worker did not exit within %ss after its %ss request timeout: %s"
                    % (WORKER_SHUTDOWN_GRACE, timeout, exc))
        finally:
            if retry_spec is not None:
                retry_spec.unlink(missing_ok=True)
    return run


def nonnegative_int(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="tasks/manifest.json")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--retries", type=nonnegative_int, default=2)
    parser.add_argument("--timeout", type=positive_int, default=600)
    parser.add_argument("--model")
    parser.add_argument("--url")
    parser.add_argument("--api", choices=["ollama", "openai"])
    args = parser.parse_args()

    manifest_path = pathlib.Path(args.manifest)
    try:
        tasks = parse_manifest(json.loads(manifest_path.read_text()))
        topological_order(tasks)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("[gemma-batch] invalid manifest: %s" % exc, file=sys.stderr)
        return 2

    worker_args = []
    for flag, value in (("--model", args.model), ("--url", args.url),
                        ("--api", args.api)):
        if value:
            worker_args += [flag, value]

    try:
        results = execute_batch(
            tasks, manifest_path.parent, pathlib.Path(args.project_root),
            make_subprocess_runner(worker_args, args.timeout), retries=args.retries)
    except (OSError, ValueError) as exc:
        print("[gemma-batch] cannot start batch: %s" % exc, file=sys.stderr)
        return 2
    output = {task_id: dataclasses.asdict(result)
              for task_id, result in results.items()}
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if all(result.status == "passed" for result in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

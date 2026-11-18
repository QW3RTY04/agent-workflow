# Agent Workflow

An optional local Codex workflow hook and live activity viewer for research, writing, coding, and other tasks with separable steps. Python standard library only; no hosted backend or telemetry is implemented.

## How it works

A SessionStart hook adds a small routing reminder to root chats. During normal work the assistant decides whether to offer a delegated workflow. Activation requires an affirmative answer to that offer or an explicit workflow request. Simple tasks and declined continuations should skip activation.

After acceptance, the assistant creates a task graph, opens its local live view, and assigns real workers through native agent tools. The view shows recorded model/effort, activity, progress, and available readable summaries. Refreshing the view makes no model calls. The hook itself does not spawn workers.

## Requirements and limitations

- Python 3.10+; the prepared copy was tested with Python 3.12 on Windows.
- A Codex client/CLI supporting local plugins, SessionStart hooks, and native agent tools.
- Compatible local Codex records (`state_5.sqlite` and rollout JSONL files). These schemas are implementation details and may change.
- The inherited supervisor policy requires `gpt-6.1-sol` at medium effort. Routine worker instructions reference `gpt-6-luna`; escalation references `gpt-6-astra`. Availability depends on the client/account. These are inherited identifiers, not a claim that they are publicly available. Review the instruction modules and `workflow_control.py` together before adapting that policy. Never silently substitute or mislabel a model.

This is an experimental source distribution. The deterministic tests do not establish end-to-end native hook activation, model availability, or delegated execution on another computer. Startup routing intentionally fails quiet when metadata is unavailable. Stop/Pause and native permissions remain authoritative.

## Setup

Clone or unpack into a stable local directory. Run commands from that directory:

```powershell
python -m unittest discover -v
python install_hook.py --build-only
```

The second command creates ignored, machine-specific files under `plugin/`; it does not install anything. Review the generated files. To install on your own machine:

```powershell
python install_hook.py
```

The installer uses the native Codex plugin commands. Review and allow the specific workflow hook through native Hooks settings. It does not bypass trust or approve other hooks. Start or resume a root chat afterward. If CLI commands or schemas differ, adapt the integration before installation. Moving the source directory requires rebuilding/reinstalling its generated references.

## Task decisions and activation

Record only actual user decisions, using exact root-chat and stable task IDs:

```powershell
python hook/state.py --help
python hook/state.py offer --session ROOT_ID --task TASK_ID
python hook/state.py accept --session ROOT_ID --task TASK_ID
```

For an explicit workflow request without an earlier offer, acceptance takes `--explicit`. The helper also supports decline, revoke, suppress, and enable. State is an advisory ledger; it does not grant permissions or prove consent on its own.

After acceptance, follow `hook/modules/activation.md`. The assistant must use a supervisor whose actual local records match policy, prepare a specification like `examples/task-spec.json`, and run:

```powershell
python module_registry.py plan progress
python workflow_control.py create --session ROOT_ID --task TASK_ID --spec examples/task-spec.json --open
```

A pinned direct-child supervisor can be supplied with `--supervisor CHILD_ID`. Creation validates existing metadata; it does not create the supervisor. The assistant starts workers separately and records assignments with the `assign` subcommand. Each task gets a distinct loopback URL at port 8768. Use native controls to stop agents; closing the view does not stop work.

## Files

- `hook/router.py` and `hook/state.py`: bounded startup routing and decision ledger.
- `hook/modules/`: selectively loaded planning, activation, evidence, progress, and escalation instructions.
- `workflow_control.py`: consent/model validation, per-task creation, assignments, and graph updates.
- `workflow_service.py`, `monitor.py`, `index.html`: loopback service and activity view.
- `task_diagram.py`: deterministic graph validation and rendering with no network dependencies.
- `config.example.json`: public role defaults; contains no chat IDs or machine paths.
- `test_*.py`, `hook/test_router.py`: tests using synthetic records and temporary state.

## Privacy and publishing

The view reads local records in read-only mode and excludes user prompts, analysis-channel text, raw reasoning, tool arguments, and tool outputs from its response. Allowed assistant text, titles, and readable summaries can still contain sensitive material. The loopback service has no authentication; other processes on the same computer may access it. Do not expose its port publicly.

Never publish `runs/`, `hook-data/`, generated `plugin/`, local configurations, logs, databases, rollout files, or screenshots of real sessions. `.gitignore` excludes these common outputs, but review every staged change before pushing. See `PUBLICATION.md` for this copy's preparation scope and verification limits.

No distribution license has been selected in this copy. Choose a license you are authorized to apply before inviting reuse.

# Report real progress

Emit one visible line at an actual step change, blocker or completion:

`[workflow:read-files] {"status":"running","current":"Read the assigned source files","next":"Return verified facts"}`

Use the exact node ID from the current task spec. States: running, waiting, completed, failed, stopped. Current is a short actual action/result; next is an intention, not hidden thinking. Human steps stay waiting until the human acts. Do not add sleeps or extra model calls to make the display animate. No fabricated summaries: the monitor reads available readable summaries from native records. Never print private raw reasoning or secrets in progress updates.

Keep model/effort and task names accurate. A completed agent turn is not by itself proof an output passed its checks. If blocked or failed, use that state explicitly. Stop/Pause means stop; never auto-restart work merely to restore a display.

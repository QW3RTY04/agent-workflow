"""Build and install one local Codex plugin. Native hook trust remains required."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from monitor import ROOT

PLUGIN = ROOT / 'plugin'


def write(relative, value):
    path = PLUGIN / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-only', action='store_true', help='Generate local plugin files without installing')
    args = parser.parse_args()
    write('.codex-plugin/plugin.json', dict(name='agent-workflow', version='0.1.0', description='Offer an efficient delegated workflow and open a task-specific live view after acceptance.', skills='./skills/', hooks='./hooks/hooks.json'))
    write('.claude-plugin/marketplace.json', dict(name='local-agent-workflow', owner=dict(name='Local owner'), plugins=[dict(name='agent-workflow', source='./', description='Optional workflow offer and live view')]))
    command = '"' + sys.executable + '" "' + str(ROOT / 'hook' / 'router.py') + '"'
    write('hooks/hooks.json', dict(hooks=dict(SessionStart=[dict(matcher='^(startup|resume|clear|compact|fork)$', hooks=[dict(type='command', command=command, commandWindows=command, timeout=3, additionalContextLimit=400, statusMessage='Load workflow offer rules')])])))
    skill = PLUGIN / 'skills' / 'agent-workflow' / 'SKILL.md'
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text('---\nname: agent-workflow\ndescription: Activate the optional agent workflow after the user accepts its offer, or explicitly requests it. Includes a task-specific live view; do not load for simple tasks or before acceptance.\n---\n\nRead `' + str(ROOT / 'hook' / 'modules' / 'activation.md') + '` only after acceptance. Use the session and task IDs from the current hook reminder/decision. Do not load all project documents.\n', encoding='utf-8')
    if args.build_only:
        print('Generated plugin files; no installation or trust changes.')
        raise SystemExit(0)
    executable = shutil.which('codex')
    if not executable:
        raise SystemExit('Codex CLI unavailable')
    # Use native installation; do not edit trust hashes or existing hook definitions.
    for args in ([executable, 'plugin', 'marketplace', 'add', str(PLUGIN), '--json'],
                 [executable, 'plugin', 'add', 'agent-workflow@local-agent-workflow', '--json']):
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8')
        print(result.stdout or result.stderr)
        if result.returncode:
            raise SystemExit(result.returncode)
    print('Installed. Review only agent-workflow in native Settings > Hooks or /hooks before expecting it to run.')

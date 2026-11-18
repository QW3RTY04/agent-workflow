"""Resolve a small instruction graph and read only selected module files."""
import argparse
import hashlib
import json
from pathlib import Path
from monitor import ROOT


def resolve(selected, registry=None):
    registry = registry or json.loads((ROOT / 'hook' / 'modules.json').read_text(encoding='utf-8'))
    output, visiting, done = [], set(), set()
    def visit(key):
        if key in done:
            return
        if key in visiting:
            raise ValueError('Instruction dependency cycle: ' + key)
        if key not in registry:
            raise ValueError('Unknown instruction module: ' + key)
        visiting.add(key)
        item = registry[key]
        for dep in item.get('requires', []):
            visit(dep)
        path = (ROOT / item['path']).resolve()
        if ROOT not in path.parents or not path.is_file():
            raise ValueError('Instruction path is missing or outside this project')
        content = path.read_text(encoding='utf-8')
        output.append(dict(id=key, path=str(path), sha256=hashlib.sha256(content.encode()).hexdigest(), content=content))
        visiting.remove(key)
        done.add(key)
    for key in selected:
        visit(key)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('modules', nargs='+')
    args = parser.parse_args()
    print(json.dumps(resolve(args.modules), ensure_ascii=False))

"""Check portable generation without installing a plugin or changing trust."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class DistributionTests(unittest.TestCase):
    def test_build_only_resolves_relocated_source_and_never_installs(self):
        with tempfile.TemporaryDirectory(prefix="workflow-copy-") as folder:
            copy = Path(folder)
            for name in ("install_hook.py", "monitor.py"):
                shutil.copy2(ROOT / name, copy / name)
            result = subprocess.run([sys.executable, str(copy / "install_hook.py"), "--build-only"],
                                    capture_output=True, text=True, check=True)
            self.assertIn("no installation or trust changes", result.stdout)
            hooks = json.loads((copy / "plugin/hooks/hooks.json").read_text())
            command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            self.assertIn(str(copy / "hook/router.py"), command)
            self.assertNotIn(str(ROOT), command)
            skill = (copy / "plugin/skills/agent-workflow/SKILL.md").read_text()
            self.assertIn(str(copy / "hook/modules/activation.md"), skill)

    def test_public_config_contains_only_role_defaults(self):
        config = json.loads((ROOT / "config.example.json").read_text())
        self.assertEqual(set(config), {"routing"})
        for role in config["routing"]:
            self.assertEqual(role["steps"], [])


if __name__ == "__main__":
    unittest.main()

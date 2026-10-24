"""Exercise installer identity preservation without Xcode, certificates, or network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "ios/setup-ios.sh"


@unittest.skipUnless(shutil.which("bash"), "The Mac installer requires bash")
class IOSInstallerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vision-ios-installer-test-")
        self.root = Path(self.temporary.name)
        self.ios = self.root / "repository/ios"
        (self.ios / "App").mkdir(parents=True)
        shutil.copyfile(SCRIPT, self.ios / "setup-ios.sh")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.developer = self.root / "Xcode.app/Contents/Developer"
        (self.developer / "usr/bin").mkdir(parents=True)
        self.write_executable(self.bin / "uname", "#!/bin/bash\nprintf 'Darwin\\n'\n")
        self.write_executable(self.developer / "usr/bin/xcodebuild", "#!/bin/bash\nexit 0\n")
        self.write_executable(self.bin / "security", "#!/bin/bash\nexit 0\n")
        self.write_executable(self.bin / "xcrun", "#!" + sys.executable + "\n" + textwrap.dedent("""\
            import sys
            print(sys.executable if sys.argv[1] == '--find' else '/mock/iPhoneOS.sdk')
        """))
        # Treat the generator as the installer dependency it is: capture its
        # identity arguments and return the public metadata the installer saves.
        (self.ios / "generate_project.py").write_text(textwrap.dedent("""\
            import argparse, json, pathlib
            parser = argparse.ArgumentParser()
            parser.add_argument('--team', default='')
            parser.add_argument('--bundle-id', default='')
            parser.add_argument('--server-url', default='https://server.example')
            parser.add_argument('--web-url', default='https://web.example/')
            parser.add_argument('--push', action='store_true')
            parser.add_argument('--self-check', action='store_true')
            args = parser.parse_args()
            bundle = args.bundle_id or ('com.jordanrapp.Vision.' + args.team.lower() if args.team else 'com.jordanrapp.Vision')
            directory = pathlib.Path(__file__).parent / 'Generated'
            directory.mkdir(exist_ok=True)
            (directory / 'project-settings.json').write_text(json.dumps({
                'teamID': args.team, 'bundleID': bundle, 'pushEnabled': args.push,
                'serverURL': args.server_url, 'webURL': args.web_url,
            }))
        """))
        self.environment = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                                DEVELOPER_DIR=str(self.developer))
        self.environment.pop("VISION_IOS_UPDATED_THIS_RUN", None)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write_executable(path, content):
        path.write_text(content)
        path.chmod(0o755)

    def run_setup(self, *options):
        result = subprocess.run([shutil.which("bash"), str(self.ios / "setup-ios.sh"), "--generate-only", *options],
                                env=self.environment, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads((self.ios / "Local/installer-settings.json").read_text())

    def choose_team_in_xcode(self, *teams):
        project = self.ios / "Vision.xcodeproj"
        project.mkdir(exist_ok=True)
        lines = ['// !$*UTF8*$!', '{']
        for team in teams:
            # Xcode normalizes quoted OpenStep keys/values when saving. Both
            # its spelling and the generator's spelling must be recognized.
            lines.extend([f'  DEVELOPMENT_TEAM = {team};', f'  "DEVELOPMENT_TEAM" = "{team}";'])
        lines.append('}')
        (project / "project.pbxproj").write_text("\n".join(lines) + "\n")

    def test_manual_first_install_keeps_the_same_bundle_on_renewal(self):
        first = self.run_setup()
        self.assertEqual(first["team"], "")
        self.assertEqual(first["bundle"], "com.jordanrapp.Vision")
        self.choose_team_in_xcode("ABCDE12345")
        renewed = self.run_setup()
        self.assertEqual(renewed["team"], "ABCDE12345")
        self.assertEqual(renewed["bundle"], first["bundle"])
        self.assertEqual(self.run_setup()["bundle"], first["bundle"])

    def test_explicit_same_team_preserves_identity_and_different_team_wins(self):
        first = self.run_setup()
        self.choose_team_in_xcode("ABCDE12345")
        same = self.run_setup("--team", "ABCDE12345")
        self.assertEqual(same["bundle"], first["bundle"])
        (self.ios / "Local/installer-settings.json").write_text(json.dumps(first))
        different = self.run_setup("--team", "ZYXWV98765")
        self.assertEqual(different["team"], "ZYXWV98765")
        self.assertEqual(different["bundle"], "com.jordanrapp.Vision.zyxwv98765")

    def test_multiple_teams_and_ci_fixture_are_not_silently_adopted(self):
        self.run_setup()
        self.choose_team_in_xcode("ABCDE12345", "ZYXWV98765")
        self.assertEqual(self.run_setup()["team"], "")
        self.choose_team_in_xcode("TESTTEAM01")
        self.assertEqual(self.run_setup()["team"], "")


if __name__ == "__main__":
    unittest.main()

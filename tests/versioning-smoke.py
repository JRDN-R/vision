"""Version numbers use decimal carry, not an ever-growing fourth segment."""
import importlib.util
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vision_build_versioning", ROOT / "web" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

assert build.normalize_version([1, 0, 0, 20]) == [1, 0, 2, 0]
assert build.normalize_version([1, 0, 2, 10]) == [1, 0, 3, 0]
assert build.normalize_version([1, 0, 9, 10]) == [1, 1, 0, 0]
assert build.normalize_version([1, 9, 9, 10]) == [2, 0, 0, 0]
source_version = tuple(map(int, (ROOT / "web" / "version.txt").read_text().strip().split(".")))
built = (ROOT / "Vision.html").read_text(encoding="utf-8")
match = re.search(r'<meta name="vision-version" content="([^"]+)"', built)
assert match, "Missing built Vision version"
released_version = tuple(map(int, match.group(1).split(".")))
assert len(released_version) == 4
assert released_version >= source_version, (source_version, released_version)
assert all(0 <= n <= 9 for n in released_version[1:]), released_version
print("Version carry and published metadata checks passed:", match.group(1))

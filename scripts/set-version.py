#!/usr/bin/env python3
"""Set the project version everywhere it lives.

    python scripts/set-version.py 0.2.0

Updates plugin/package.json, plugin/py_modules/steamos_ha/__init__.py and
custom_components/steamos/manifest.json. Then commit and tag: git tag v0.2.0
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit("version must look like 1.2.3")

    pkg = ROOT / "plugin" / "package.json"
    data = json.loads(pkg.read_text())
    data["version"] = version
    pkg.write_text(json.dumps(data, indent=2) + "\n")

    manifest = ROOT / "custom_components" / "steamos" / "manifest.json"
    data = json.loads(manifest.read_text())
    data["version"] = version
    manifest.write_text(json.dumps(data, indent=2) + "\n")

    init = ROOT / "plugin" / "py_modules" / "steamos_ha" / "__init__.py"
    text = re.sub(r'PLUGIN_VERSION = "[^"]+"', f'PLUGIN_VERSION = "{version}"', init.read_text())
    init.write_text(text)

    print(f"version set to {version} in package.json, manifest.json, steamos_ha/__init__.py")
    print(f"next: git commit -am 'Release {version}' && git tag v{version} && git push --tags")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])

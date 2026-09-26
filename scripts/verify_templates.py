"""Run: python -m scripts.verify_templates"""

import sys

from app.config import settings
from app.services.templates.loader import load_manifest


def main() -> None:
    root = settings.templates_path
    manifest = load_manifest()
    missing: list[str] = []
    for key, filename in manifest.items():
        path = root / filename
        if not path.is_file():
            missing.append(f"{key} -> {path}")
    if missing:
        print("Missing templates:")
        for m in missing:
            print(f"  - {m}")
        print(f"\nCopy org files into: {root}")
        sys.exit(1)
    print(f"OK — {len(manifest)} templates in {root}")


if __name__ == "__main__":
    main()

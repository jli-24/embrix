"""Out-of-process Windows application switch and rollback helper."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--old-pid", required=True, type=int)
    args = parser.parse_args()
    from embedded_copilot.production_release.update_switch import switch_after_exit

    result = switch_after_exit(args.root, old_pid=args.old_pid)
    return 0 if result["state"] == "BOOT_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

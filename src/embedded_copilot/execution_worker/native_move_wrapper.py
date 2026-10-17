"""Fixed -I -S pcbnew read-back resource; invoked only by the dedicated bound runner."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import importlib.util
import json
import os
from pathlib import Path
from uuid import UUID


def main() -> int:
    if len(sys.argv) != 3:
        raise ValueError("fixed native read-back argv required")
    board_path = Path(sys.argv[1])
    native_uuid = str(UUID(sys.argv[2]))
    if not board_path.is_absolute() or native_uuid != sys.argv[2]:
        raise ValueError("fixed native read-back target invalid")
    bundle_root = Path(__file__).resolve().parent
    if (bundle_root / "python311._pth").read_bytes() != b"python311.zip\nLib\nLib/site-packages\n.\n":
        raise ValueError("fixed Python loading roots unavailable")
    sys.path[:] = [
        str(bundle_root / "python311.zip"),
        str(bundle_root / "Lib"),
        str(bundle_root / "Lib" / "site-packages"),
        str(bundle_root),
    ]
    dll_handles = [os.add_dll_directory(str(bundle_root))]
    dll_dir = bundle_root / "DLLs"
    if dll_dir.is_dir():
        dll_handles.append(os.add_dll_directory(str(dll_dir)))
    core_path = bundle_root / "native_move_native_core.py"
    pcbnew_path = bundle_root / "pcbnew.py"
    extension_path = bundle_root / "_pcbnew.pyd"
    if not all(path.is_file() and path.parent == bundle_root for path in (core_path, pcbnew_path, extension_path)):
        raise ValueError("fixed native read-back module bundle unavailable")
    spec = importlib.util.spec_from_file_location("_embrix_native_move_core", core_path)
    if spec is None or spec.loader is None:
        raise ValueError("fixed native read-back core unavailable")
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    extension_spec = importlib.util.spec_from_file_location("_pcbnew", extension_path)
    if extension_spec is None or extension_spec.loader is None:
        raise ValueError("fixed native extension unavailable")
    extension = importlib.util.module_from_spec(extension_spec)
    sys.modules["_pcbnew"] = extension
    extension_spec.loader.exec_module(extension)
    pcbnew_spec = importlib.util.spec_from_file_location("pcbnew", pcbnew_path)
    if pcbnew_spec is None or pcbnew_spec.loader is None:
        raise ValueError("fixed native Python module unavailable")
    pcbnew = importlib.util.module_from_spec(pcbnew_spec)
    sys.modules["pcbnew"] = pcbnew
    pcbnew_spec.loader.exec_module(pcbnew)
    if Path(pcbnew.__file__).resolve() != pcbnew_path or Path(extension.__file__).resolve() != extension_path:
        raise ValueError("native module path drift")

    document = core.load_native_projection(pcbnew, str(board_path), native_uuid)
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > 64 * 1024:
        raise ValueError("native read-back stdout exceeds fixed limit")
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

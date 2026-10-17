"""Portable bundle contracts and fake loader execution; no native tools or modules."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
from dataclasses import FrozenInstanceError, replace
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.execution_worker import native_move_native_core as native_core
from embedded_copilot.execution_worker import native_move_pcbnew_reader as reader_module
from embedded_copilot.execution_worker import native_move_wrapper as wrapper_module
from embedded_copilot.execution_worker.native_move_pcbnew_reader import (
    NativeBundleFile,
    NativeReadbackBundleV1,
)
from tests.execution_worker.test_c2r_native_move_offline import (
    UUID,
    FakeNativeApi,
    FakeNativeBoard,
)


@pytest.fixture
def portable_bundle(tmp_path):
    root = tmp_path / "portable-python311"
    root.mkdir()
    reviewed = Path(reader_module.__file__).parent
    content = {
        "python.exe": b"fake portable python311 interpreter",
        "python311._pth": b"python311.zip\nLib\nLib/site-packages\n.\n",
        "python311.dll": b"fake python311 DLL",
        "python311.zip": b"fake standard library archive",
        "pcbnew.py": b"# fake native API module; never imported\n",
        "_pcbnew.pyd": b"fake native extension; never loaded",
        "DLLs/native-dependency.dll": b"fake reviewed native dependency",
        "Lib/os.py": b"# fake standard library module\n",
        "native_move_wrapper.py": (reviewed / "native_move_wrapper.py").read_bytes(),
        "native_move_native_core.py": (reviewed / "native_move_native_core.py").read_bytes(),
    }
    for relative, data in content.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "Lib/site-packages").mkdir()
    environment = {"PATH": str(root), "TEMP": str(tmp_path)}
    return root, environment


def _freeze(portable_bundle):
    return NativeReadbackBundleV1.freeze(*portable_bundle)


def test_freeze_has_stable_inventory_hash_and_reader_version(portable_bundle):
    root, environment = portable_bundle
    first = _freeze(portable_bundle)
    second = NativeReadbackBundleV1.freeze(root, dict(reversed(tuple(environment.items()))))

    assert first.check() and second.check()
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.reader_version == "native-v2-bundle-" + first.fingerprint.removeprefix("sha256:")
    assert first.interpreter == root / "python.exe"
    assert first.wrapper == root / "native_move_wrapper.py"
    for entry in first.files:
        assert entry.byte_size == len(entry.absolute_path.read_bytes())
        assert entry.sha256 == "sha256:" + hashlib.sha256(entry.absolute_path.read_bytes()).hexdigest()
    copied_environment = first.environment_dict()
    copied_environment["PATH"] = "changed"
    assert first.check() and first.environment_dict() == environment


def test_refreeze_changed_dependency_changes_identity(portable_bundle):
    root, _ = portable_bundle
    original = _freeze(portable_bundle)
    (root / "DLLs/native-dependency.dll").write_bytes(b"different fake dependency")

    updated = _freeze(portable_bundle)

    assert not original.check()
    assert updated.check()
    assert updated.fingerprint != original.fingerprint
    assert updated.reader_version != original.reader_version


def test_bundle_and_file_are_frozen_and_reject_extra_constructor_fields(portable_bundle):
    bundle = _freeze(portable_bundle)
    with pytest.raises(FrozenInstanceError):
        bundle.root = Path("changed")
    with pytest.raises(FrozenInstanceError):
        bundle.files[0].byte_size = 0
    with pytest.raises(TypeError):
        NativeReadbackBundleV1(root=bundle.root, files=bundle.files, environment=bundle.environment, extra=True)
    entry = bundle.files[0]
    with pytest.raises(TypeError):
        NativeBundleFile(role=entry.role, absolute_path=entry.absolute_path,
                         byte_size=entry.byte_size, sha256=entry.sha256, extra=True)


@pytest.mark.parametrize("relative", [
    "python.exe", "native_move_wrapper.py", "native_move_native_core.py", "pcbnew.py",
    "_pcbnew.pyd", "DLLs/native-dependency.dll", "python311.dll", "python311.zip", "Lib/os.py",
])
def test_frozen_inventory_detects_each_required_or_dependency_byte_drift(portable_bundle, relative):
    root, _ = portable_bundle
    bundle = _freeze(portable_bundle)
    path = root / relative
    path.write_bytes(path.read_bytes() + b"changed")
    assert not bundle.check()


@pytest.mark.parametrize("relative", ["native_move_wrapper.py", "native_move_native_core.py"])
def test_freeze_rejects_unreviewed_resource_bytes(portable_bundle, relative):
    root, environment = portable_bundle
    (root / relative).write_bytes(b"# unreviewed resource\n")
    with pytest.raises(ValueError, match="closure unavailable"):
        NativeReadbackBundleV1.freeze(root, environment)


@pytest.mark.parametrize("field,value", [
    ("byte_size", -1), ("byte_size", True), ("byte_size", 1.5), ("byte_size", "1"),
    ("sha256", "sha256:" + "0" * 64), ("sha256", 7), ("role", "UNKNOWN"),
])
def test_bundle_check_rejects_invalid_file_identity(portable_bundle, field, value):
    bundle = _freeze(portable_bundle)
    entries = list(bundle.files)
    entries[0] = replace(entries[0], **{field: value})
    assert not replace(bundle, files=tuple(entries)).check()


@pytest.mark.parametrize("change", ["duplicate_file", "outside_root", "reordered_files", "duplicate_env", "reordered_env"])
def test_bundle_check_rejects_noncanonical_inventory(portable_bundle, change):
    bundle = _freeze(portable_bundle)
    if change == "duplicate_file":
        bundle = replace(bundle, files=bundle.files + (bundle.files[0],))
    elif change == "outside_root":
        entries = list(bundle.files)
        entries[0] = replace(entries[0], absolute_path=bundle.root.parent / "outside.dll")
        bundle = replace(bundle, files=tuple(entries))
    elif change == "reordered_files":
        bundle = replace(bundle, files=tuple(reversed(bundle.files)))
    elif change == "duplicate_env":
        bundle = replace(bundle, environment=bundle.environment + (bundle.environment[0],))
    else:
        bundle = replace(bundle, environment=tuple(reversed(bundle.environment)))
    assert not bundle.check()


@pytest.mark.parametrize("change", ["wrong_path", "pythonpath", "empty_temp", "nul_temp", "non_string", "lowercase", "extra"])
def test_freeze_rejects_unapproved_environment(portable_bundle, change):
    root, environment = portable_bundle
    environment = dict(environment)
    if change == "wrong_path":
        environment["PATH"] = str(root.parent)
    elif change == "pythonpath":
        environment["PYTHONPATH"] = str(root)
    elif change == "empty_temp":
        environment["TEMP"] = ""
    elif change == "nul_temp":
        environment["TEMP"] = "bad\x00path"
    elif change == "non_string":
        environment["TEMP"] = 7
    elif change == "lowercase":
        environment["temp"] = str(root)
    else:
        environment["UNAPPROVED"] = "value"
    with pytest.raises(ValueError, match="closure unavailable"):
        NativeReadbackBundleV1.freeze(root, environment)


@pytest.mark.parametrize("relative", [
    "python.exe", "native_move_wrapper.py", "native_move_native_core.py", "pcbnew.py", "_pcbnew.pyd",
])
def test_freeze_rejects_required_files_outside_fixed_loading_root(portable_bundle, relative):
    root, environment = portable_bundle
    unloaded = root / "not-a-loading-root"
    unloaded.mkdir()
    (root / relative).rename(unloaded / relative)
    with pytest.raises(ValueError, match="closure unavailable"):
        NativeReadbackBundleV1.freeze(root, environment)


@pytest.mark.parametrize("relative", [
    "python.exe", "python311._pth", "python311.zip", "python311.dll", "Lib/os.py", "pcbnew.py", "_pcbnew.pyd",
    "native_move_wrapper.py", "native_move_native_core.py",
])
def test_freeze_rejects_missing_closure_files(portable_bundle, relative):
    root, environment = portable_bundle
    (root / relative).unlink()
    with pytest.raises(ValueError, match="closure unavailable"):
        NativeReadbackBundleV1.freeze(root, environment)


@pytest.mark.parametrize("pth", [
    b"python311.zip\nLib\nLib/site-packages\n.\nimport site\n",
    b"python311.zip\nLib\nLib/site-packages\n../outside\n",
    b"python311.zip\nLib\n.\n", b"python312.zip\nLib\nLib/site-packages\n.\n",
])
def test_freeze_rejects_changed_python_startup_loading_roots(portable_bundle, pth):
    root, environment = portable_bundle
    (root / "python311._pth").write_bytes(pth)
    with pytest.raises(ValueError, match="closure unavailable"):
        NativeReadbackBundleV1.freeze(root, environment)


def test_freeze_rejects_site_pth_injection(portable_bundle):
    root, environment = portable_bundle
    (root / "Lib/site-packages/injection.pth").write_bytes(b"import site\n")
    with pytest.raises(ValueError, match="closure unavailable"):
        NativeReadbackBundleV1.freeze(root, environment)


@pytest.mark.parametrize("change", ["new_file", "removed_file", "renamed_interpreter", "removed_site_packages"])
def test_check_detects_closed_inventory_or_loading_directory_drift(portable_bundle, change):
    root, _ = portable_bundle
    bundle = _freeze(portable_bundle)
    if change == "new_file":
        (root / "unreviewed.dll").write_bytes(b"fake added dependency")
    elif change == "removed_file":
        (root / "DLLs/native-dependency.dll").unlink()
    elif change == "renamed_interpreter":
        (root / "python.exe").rename(root / "python3.exe")
    else:
        (root / "Lib/site-packages").rmdir()
    assert not bundle.check()


@pytest.mark.parametrize("fault", ["symlink", "reparse"])
def test_bundle_rejects_fake_reparse_paths(portable_bundle, monkeypatch, fault):
    root, environment = portable_bundle
    bundle = _freeze(portable_bundle)
    target = root / "DLLs/native-dependency.dll"
    if fault == "symlink":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda self: self == target or original(self))
    else:
        original_stat = Path.stat

        class ReparseStat:
            st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT

            def __init__(self, wrapped):
                self.wrapped = wrapped

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

        def fake_stat(self, *args, **kwargs):
            result = original_stat(self, *args, **kwargs)
            return ReparseStat(result) if self == target else result

        monkeypatch.setattr(Path, "stat", fake_stat)
    assert not bundle.check()
    with pytest.raises(ValueError, match="reparse path"):
        NativeReadbackBundleV1.freeze(root, environment)


def test_freeze_rejects_relative_root(portable_bundle):
    _, environment = portable_bundle
    with pytest.raises(ValueError, match="root unavailable"):
        NativeReadbackBundleV1.freeze(Path("relative-bundle"), environment)


def test_wrapper_uses_fixed_module_specs_before_shadow_package(portable_bundle, monkeypatch, tmp_path):
    root, _ = portable_bundle
    shadow = root / "Lib/site-packages/pcbnew/__init__.py"
    shadow.parent.mkdir()
    shadow.write_bytes(b"raise AssertionError('shadow pcbnew package executed')\n")
    board_path = tmp_path / "board.kicad_pcb"
    board_path.write_bytes(b"fake board bytes; getter fixture supplies native geometry")
    native_api = FakeNativeApi(FakeNativeBoard())
    output = io.BytesIO()
    loaded = []
    dll_roots = []

    class FakeLoader:
        def __init__(self, path):
            self.path = Path(path)

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            loaded.append(self.path)
            module.__file__ = str(self.path)
            if self.path == root / "native_move_native_core.py":
                module.load_native_projection = native_core.load_native_projection
            elif self.path == root / "pcbnew.py":
                module.PAD_ATTRIB_SMD = native_api.PAD_ATTRIB_SMD
                module.PAD_SHAPE_RECT = native_api.PAD_SHAPE_RECT
                module.LoadBoard = native_api.LoadBoard
            elif self.path != root / "_pcbnew.pyd":
                pytest.fail("wrapper selected an unreviewed module path")

    def fixed_spec(name, path):
        return ModuleSpec(name, FakeLoader(path), origin=str(path))

    # main mutates these process globals; the context restores all originals.
    with monkeypatch.context() as scoped:
        scoped.setattr(wrapper_module, "__file__", str(root / "native_move_wrapper.py"))
        scoped.setattr(wrapper_module.importlib.util, "spec_from_file_location", fixed_spec)
        scoped.setattr(wrapper_module.os, "add_dll_directory", lambda path: dll_roots.append(path))
        scoped.setattr(sys, "argv", [str(root / "native_move_wrapper.py"), str(board_path), UUID])
        scoped.setattr(sys, "stdout", SimpleNamespace(buffer=output))
        scoped.setattr(sys, "path", list(sys.path))
        scoped.setitem(sys.modules, "pcbnew", SimpleNamespace())
        scoped.setitem(sys.modules, "_pcbnew", SimpleNamespace())
        assert wrapper_module.main() == 0
        assert sys.path == [str(root / "python311.zip"), str(root / "Lib"),
                            str(root / "Lib/site-packages"), str(root)]
    assert loaded == [root / "native_move_native_core.py", root / "_pcbnew.pyd", root / "pcbnew.py"]
    assert shadow not in loaded
    assert native_api.loaded == [str(board_path)]
    assert dll_roots == [str(root), str(root / "DLLs")]
    document = json.loads(output.getvalue())
    assert document["native_uuid"] == UUID
    assert document["x_mm"] == 11.0 and document["y_mm"] == 20.0

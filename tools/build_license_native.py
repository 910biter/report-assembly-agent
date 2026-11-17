"""Build target-Python native license modules into a separate staging tree.

Requires a C compiler and license-build-requirements.txt on the BUILD machine.
No compiler, issuer tool, C source or signing private key ships to customers.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import shutil
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", choices=["msvc", "mingw32", "unix"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output == root or root in output.parents and output.parts[len(root.parts)] == "app":
        parser.error("Build to a staging directory, not the source app tree")
    from Cython.Build import cythonize
    from setuptools import Distribution, Extension

    sources = ["_trust.pyx", "protocol.py", "service.py", "storage.py"]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ira-license-build-") as temporary:
        extensions = [Extension(f"app.licensing.{Path(name).stem}",
                      [str(root / "app" / "licensing" / name)]) for name in sources]
        extensions = cythonize(extensions, build_dir=str(Path(temporary) / "c"),
            compiler_directives={"language_level": 3, "binding": True,
                                 "annotation_typing": False, "embedsignature": False},
            force=True)
        distribution = Distribution({"name": "ira-license-core", "ext_modules": extensions})
        command = distribution.get_command_obj("build_ext")
        command.build_lib = str(output)
        command.build_temp = str(Path(temporary) / "objects")
        if args.compiler:
            command.compiler = args.compiler
        distribution.run_command("build_ext")
    target = output / "app" / "licensing"
    for name in sources:
        stem = Path(name).stem
        if not any((target / (stem + suffix)).is_file()
                   for suffix in importlib.machinery.EXTENSION_SUFFIXES):
            raise RuntimeError(f"Missing compiled module: {stem}")
    shutil.copy2(root / "app" / "licensing" / "__init__.py", target / "__init__.py")
    print(f"Native license modules ready: {target}")


if __name__ == "__main__":
    main()

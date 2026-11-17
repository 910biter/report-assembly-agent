"""Run with IRA_NATIVE_TEST_DIR=.native-build after native compilation."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("damage", ["none", "missing", "source", "corrupt"])
def test_compiled_release_trust_boundary(tmp_path, damage):
    source = os.environ.get("IRA_NATIVE_TEST_DIR")
    if not source:
        pytest.skip("Native artifact test requires IRA_NATIVE_TEST_DIR")
    native = Path(source).resolve() / "app" / "licensing"
    assert native.is_dir()
    target = tmp_path / "app" / "licensing"
    shutil.copytree(native, target)
    (target.parent / "__init__.py").write_text("")
    # This former trust source must have no effect on the compiled verifier.
    (target / "trusted_keys.json").write_text(json.dumps({"attacker": "anything"}))
    trust_files = list(target.glob("_trust*.so")) + list(target.glob("_trust*.pyd"))
    assert len(trust_files) == 1
    if damage in {"missing", "source"}:
        trust_files[0].unlink()
    if damage == "source":
        (target / "_trust.py").write_text("def verify(token): return True\n")
    if damage == "corrupt":
        trust_files[0].write_bytes(b"not a native module")
    code = '''
from pathlib import Path
from importlib.machinery import ExtensionFileLoader
from app.licensing import service, protocol, storage
assert all(isinstance(m.__loader__, ExtensionFileLoader) for m in (service, protocol, storage))
s = service.LicenseService(Path('state'), machine=lambda: 'host')
'''
    if damage == "none":
        code += '''
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
private = Ed25519PrivateKey.generate()
public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
Path('app/licensing/trusted_keys.json').write_text(__import__('json').dumps({'attacker': protocol.encode(public)}))
token = protocol.sign('IRA-LIC1', {'key_id':'attacker'}, private)
assert s.status()['status'] == 'unlicensed'
try:
    s.activate(token)
except protocol.LicenseError as exc:
    assert str(exc) == 'UNKNOWN_SIGNING_KEY'
else:
    raise AssertionError('External trust file was accepted')
'''
    else:
        code += '''
assert s.status()['status'] == 'license_module_unavailable'
try:
    s.activate('anything')
except protocol.LicenseError as exc:
    assert str(exc) == 'LICENSE_MODULE_UNAVAILABLE'
else:
    raise AssertionError('Native verifier bypassed')
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

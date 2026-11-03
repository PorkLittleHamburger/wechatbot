"""Offline kernel DACL/inheritance/AccessCheck tests; no WeChat or elevation."""
import ctypes
from ctypes import wintypes as w
from contextlib import contextmanager
from pathlib import Path
import re
import struct
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows DACL tests")
HELPER = Path(__file__).resolve().parents[1] / "vendor" / "helper"
PRIVATE_DACL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)"


class SidAttributes(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", w.DWORD)]


class Mapping(ctypes.Structure):
    _fields_ = [("read", w.DWORD), ("write", w.DWORD),
                ("execute", w.DWORD), ("all", w.DWORD)]


class WindowsSecurity:
    """Independent test harness using SDDL and the real Windows access checker."""

    def __init__(self):
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        p = ctypes.c_void_p
        out = ctypes.POINTER(p)
        dword_out = ctypes.POINTER(w.DWORD)
        self.bind(self.k, "GetCurrentProcess", [], w.HANDLE)
        self.bind(self.k, "CloseHandle", [w.HANDLE], w.BOOL)
        self.bind(self.k, "LocalFree", [p], p)
        self.bind(self.a, "OpenProcessToken", [w.HANDLE, w.DWORD, out], w.BOOL)
        self.bind(self.a, "GetTokenInformation", [p, ctypes.c_int, p, w.DWORD, dword_out], w.BOOL)
        self.bind(self.a, "ConvertSidToStringSidW", [p, out], w.BOOL)
        self.bind(self.a, "ConvertStringSidToSidW", [w.LPCWSTR, out], w.BOOL)
        self.bind(self.a, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                  [w.LPCWSTR, w.DWORD, out, dword_out], w.BOOL)
        self.bind(self.a, "ConvertSecurityDescriptorToStringSecurityDescriptorW",
                  [p, w.DWORD, w.DWORD, out, dword_out], w.BOOL)
        self.bind(self.a, "GetNamedSecurityInfoW",
                  [w.LPWSTR, ctypes.c_int, w.DWORD, p, p, p, p, out], w.DWORD)
        self.bind(self.a, "SetFileSecurityW", [w.LPCWSTR, w.DWORD, p], w.BOOL)
        self.bind(self.a, "CreateRestrictedToken",
                  [p, w.DWORD, w.DWORD, p, w.DWORD, p, w.DWORD, p, out], w.BOOL)
        self.bind(self.a, "DuplicateToken", [p, ctypes.c_int, out], w.BOOL)
        self.bind(self.a, "AccessCheck", [p, p, w.DWORD, ctypes.POINTER(Mapping),
                  p, dword_out, dword_out, ctypes.POINTER(w.BOOL)], w.BOOL)

    @staticmethod
    def bind(dll, name, args, result):
        function = getattr(dll, name)
        function.argtypes, function.restype = args, result

    @staticmethod
    def check(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    @contextmanager
    def descriptor(self, sddl):
        sd = ctypes.c_void_p()
        self.check(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(sd), None))
        try:
            yield sd
        finally:
            self.k.LocalFree(sd)

    def set_private(self, directory):
        with self.descriptor(PRIVATE_DACL) as sd:
            self.check(self.a.SetFileSecurityW(str(directory), 0x80000004, sd))

    def dacl(self, path):
        sd, text = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            error = self.a.GetNamedSecurityInfoW(str(path), 1, 4, None, None,
                                                 None, None, ctypes.byref(sd))
            if error:
                raise ctypes.WinError(error)
            self.check(self.a.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                sd, 1, 4, ctypes.byref(text), None))
            return ctypes.wstring_at(text)
        finally:
            self.k.LocalFree(text)
            self.k.LocalFree(sd)

    @contextmanager
    def normal_user(self):
        """Disable BA even if the runner has it; never elevate/impersonate a thread."""
        token, restricted, impersonation = (ctypes.c_void_p() for _ in range(3))
        admin, user_string = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            self.check(self.a.OpenProcessToken(self.k.GetCurrentProcess(), 0xA,
                                               ctypes.byref(token)))
            needed = w.DWORD()
            self.a.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            self.check(self.a.GetTokenInformation(token, 1, buffer, needed,
                                                  ctypes.byref(needed)))
            user = ctypes.cast(buffer, ctypes.POINTER(SidAttributes)).contents
            self.check(self.a.ConvertSidToStringSidW(user.sid, ctypes.byref(user_string)))
            sid = ctypes.wstring_at(user_string)
            self.check(self.a.ConvertStringSidToSidW("S-1-5-32-544", ctypes.byref(admin)))
            disable = SidAttributes(admin, 0)
            self.check(self.a.CreateRestrictedToken(token, 1, 1, ctypes.byref(disable),
                0, None, 0, None, ctypes.byref(restricted)))
            self.check(self.a.DuplicateToken(restricted, 2, ctypes.byref(impersonation)))
            yield impersonation, sid
        finally:
            self.k.LocalFree(user_string)
            self.k.LocalFree(admin)
            for handle in (impersonation, restricted, token):
                if handle:
                    self.k.CloseHandle(handle)

    def access(self, token, dacl, desired):
        # Reproduce elevated owner=BA in memory, without setting an on-disk owner
        # (which would require privileges). DACL comes from the real filesystem.
        with self.descriptor("O:BAG:BA" + dacl) as sd:
            mapping = Mapping(0x120089, 0x120116, 0x1200A0, 0x1F01FF)
            privileges = ctypes.create_string_buffer(4096)
            length, granted, allowed = w.DWORD(len(privileges)), w.DWORD(), w.BOOL()
            self.check(self.a.AccessCheck(sd, token, desired, ctypes.byref(mapping),
                privileges, ctypes.byref(length), ctypes.byref(granted), ctypes.byref(allowed)))
            return bool(allowed.value)


@pytest.fixture
def media(monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    from wxbot_framework import files, voices, snapshot_access
    return files, voices, snapshot_access


@pytest.fixture
def security():
    return WindowsSecurity()


def test_real_private_dacl_grants_only_user_read_and_inherits(tmp_path, media, security):
    _, _, access = media
    owned = tmp_path / "fresh snapshot"
    owned.mkdir(mode=0o700)
    security.set_private(owned)
    before = security.dacl(owned)
    parent_before = security.dacl(tmp_path)
    with security.normal_user() as (token, sid):
        assert not security.access(token, before, 0x1200A9)
        access.grant_snapshot_read(owned)
        after = security.dacl(owned)
        assert after.startswith("D:P")
        old_aces = re.findall(r"\([^)]*\)", before)
        new_aces = re.findall(r"\([^)]*\)", after)
        for ace in old_aces:
            new_aces.remove(ace)
        assert new_aces == [f"(A;OICI;0x1200a9;;;{sid})"]
        assert security.access(token, after, 0x1200A9)
        snapshot = owned / "payload.bin"
        snapshot.write_bytes(b"offline payload")
        file_dacl = security.dacl(snapshot)
        assert security.access(token, file_dacl, 0x120089)
        assert f";;;{sid})" in file_dacl
        for dacl in (after, file_dacl):
            for denied in (2, 4, 0x10000, 0x40000, 0x80000):
                assert not security.access(token, dacl, denied)
        # An unrelated SID cannot acquire read through Users/Everyone grants.
        assert not security.access(token, after.replace(sid, "S-1-5-21-1-2-3-9999"), 1)
    assert security.dacl(tmp_path) == parent_before


@pytest.mark.parametrize("kind", ["file", "silk", "wav"])
def test_outbound_acl_precedes_write_and_handoff(tmp_path, media, security, monkeypatch, kind):
    files, voices, access = media
    source = tmp_path / "input"
    if kind == "file":
        source.write_bytes(b"sample")
    elif kind == "silk":
        source.write_bytes(voices.TENCENT_SILK_HEADER + struct.pack("<h", 3) + b"abc")
    else:
        import wave
        with wave.open(str(source), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0" * 640)

        class Encoder:
            def encode(self, pcm, output, *args, **kwargs):
                output.write(voices.TENCENT_SILK_HEADER + struct.pack("<h", 3) + b"abc")

        monkeypatch.setattr(voices, "_load_encoder", lambda: Encoder())
    module = files if kind == "file" else voices
    called = []

    def grant(owned):
        assert list(owned.iterdir()) == []
        security.set_private(owned)
        access.grant_snapshot_read(owned)
        called.append(owned)

    monkeypatch.setattr(module, "grant_snapshot_read", grant)
    prepare = files.prepare_file if kind == "file" else voices.prepare_voice
    with prepare(source, tmp_path / "outbound") as info:
        assert called == [info.path.parent]
        with security.normal_user() as (token, _):
            assert security.access(token, security.dacl(info.path.parent), 0x1200A9)
            assert security.access(token, security.dacl(info.path), 0x120089)
    assert not called[0].exists()
    assert source.exists()


@pytest.mark.parametrize("kind", ["file", "voice"])
@pytest.mark.parametrize("failure", [
    "token", "token_size", "token_user", "invalid_sid", "read_dacl", "merge", "write_dacl",
])
def test_acl_api_failure_never_yields_and_cleans_own_directory(
        tmp_path, media, monkeypatch, kind, failure):
    files, voices, access = media
    source = tmp_path / "input"
    source.write_bytes(voices.TENCENT_SILK_HEADER + struct.pack("<h", 3) + b"abc")
    root = tmp_path / "snapshots"
    root.mkdir()
    sentinel = root / "keep"
    sentinel.write_bytes(b"unrelated")
    original = access.ctypes.WinDLL
    name = {"token": "OpenProcessToken", "token_size": "GetTokenInformation",
            "token_user": "GetTokenInformation", "invalid_sid": "IsValidSid",
            "read_dacl": "GetNamedSecurityInfoW",
            "merge": "SetEntriesInAclW", "write_dacl": "SetNamedSecurityInfoW"}[failure]

    def dll(library, **kwargs):
        real = original(library, **kwargs)
        if library == "advapi32":
            function = getattr(real, name)
            def fail(*args):
                if failure == "token_user" and not args[2]:
                    return function(*args)
                ctypes.set_last_error(5)
                return 0 if failure in ("token", "token_size", "token_user", "invalid_sid") else 5
            setattr(real, name, fail)
        return real

    monkeypatch.setattr(access.ctypes, "WinDLL", dll)
    prepare = files.prepare_file if kind == "file" else voices.prepare_voice
    with pytest.raises(OSError):
        with prepare(source, root):
            pytest.fail("ACL errors must prevent handoff")
    assert list(root.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"unrelated"
    assert source.exists()

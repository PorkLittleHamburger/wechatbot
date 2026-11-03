"""Allow the unelevated same user to read a private outbound snapshot on Windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class _Trustee(ctypes.Structure):
    _fields_ = [("multiple", ctypes.c_void_p), ("operation", ctypes.c_int),
                ("form", ctypes.c_int), ("type", ctypes.c_int),
                ("sid", ctypes.c_void_p)]


class _ExplicitAccess(ctypes.Structure):
    _fields_ = [("permissions", wintypes.DWORD), ("mode", ctypes.c_int),
                ("inheritance", wintypes.DWORD), ("trustee", _Trustee)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]


def grant_snapshot_read(directory: Path) -> None:
    """Add only TokenUser read/traverse to a newly created, still empty directory.

    Python 3.13's private Windows temp DACL can name Administrators as owner
    when elevated. OWNER RIGHTS then cannot authorize an unelevated TokenUser.
    Preserve that private DACL and add a direct user ACE before creating files,
    so inherited file read access does not depend on owner or admin membership.
    Callers must own the directory and clean it up if any API fails.
    """
    if os.name != "nt":
        return
    if directory.is_symlink() or not directory.is_dir() or any(directory.iterdir()):
        raise ValueError("Snapshot ACL requires a new empty directory")

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.c_void_p
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                             pointer, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.IsValidSid.argtypes = [pointer]
    security.IsValidSid.restype = wintypes.BOOL
    security.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int,
        wintypes.DWORD, pointer, pointer, ctypes.POINTER(pointer), pointer,
        ctypes.POINTER(pointer)]
    security.GetNamedSecurityInfoW.restype = wintypes.DWORD
    security.SetEntriesInAclW.argtypes = [wintypes.ULONG,
        ctypes.POINTER(_ExplicitAccess), pointer, ctypes.POINTER(pointer)]
    security.SetEntriesInAclW.restype = wintypes.DWORD
    security.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int,
        wintypes.DWORD, pointer, pointer, pointer, pointer]
    security.SetNamedSecurityInfoW.restype = wintypes.DWORD

    token = wintypes.HANDLE()
    descriptor, new_acl = pointer(), pointer()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008,
                                          ctypes.byref(token)):  # TOKEN_QUERY
            raise ctypes.WinError(ctypes.get_last_error())
        needed = wintypes.DWORD()
        if security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed)):
            raise OSError("Unexpected empty TokenUser result")
        if ctypes.get_last_error() != 122 or needed.value < ctypes.sizeof(_TokenUser):
            raise ctypes.WinError(ctypes.get_last_error())
        user_buffer = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 1, user_buffer, needed,
                                             ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.cast(user_buffer, ctypes.POINTER(_TokenUser)).contents
        if not user.sid or not security.IsValidSid(user.sid):
            raise OSError("Invalid TokenUser SID")

        old_acl = pointer()
        error = security.GetNamedSecurityInfoW(str(directory), 1, 4, None, None,
            ctypes.byref(old_acl), None, ctypes.byref(descriptor))
        if error:
            raise ctypes.WinError(error)
        if not old_acl:
            raise OSError("Snapshot must have a private non-null DACL")
        # FILE_GENERIC_READ | FILE_TRAVERSE, OI | CI, GRANT_ACCESS.
        # No write/delete/WRITE_DAC, no group or Everyone grant.
        entry = _ExplicitAccess(0x120089 | 0x20, 1, 3,
                                _Trustee(None, 0, 0, 1, user.sid))
        error = security.SetEntriesInAclW(1, ctypes.byref(entry), old_acl,
                                          ctypes.byref(new_acl))
        if error:
            raise ctypes.WinError(error)
        error = security.SetNamedSecurityInfoW(str(directory), 1, 4, None, None,
                                                new_acl, None)
        if error:
            raise ctypes.WinError(error)
    finally:
        if new_acl:
            kernel.LocalFree(new_acl)
        if descriptor:
            kernel.LocalFree(descriptor)
        if token:
            kernel.CloseHandle(token)

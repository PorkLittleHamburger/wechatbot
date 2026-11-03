# Third-Party Notices

## helper

`vendor/helper` is vendored from:

- Repository: `https://github.com/Prometheus-first/helper`
- Commit: `a81c495770b397646ba0661118927c83589c41fc`
- License: helper Noncommercial License 1.0
- License text: `vendor/helper/LICENSE`
- Additional notices: `vendor/helper/THIRD_PARTY_NOTICES.md`

The helper license permits personal learning, research, testing, entertainment, and noncommercial self-use. It does not grant commercial rights. In particular, it does not permit sales, paid bots, paid group services, SaaS, commercial hosting, advertising/marketing use, or integration into commercial products.

The native bridge is built from the helper bridge sources and MinHook 1.3.4. It does not contain WeChat client files.

Local changes relative to the pinned helper commit:

- `native/bridge_watchdog.py` explicitly adds its native module directory before internal imports so it works with isolated embeddable Python.
- `wxbot_framework/bridge_lifecycle.py` captures watchdog startup output in `logs/bridge_watchdog_startup.log` and reports early exit codes, readiness timeouts, and absolute log paths.

- `native/run_selftest.py` and `native/filehelper_service.py` compare resolved bridge paths consistently across Windows extended drive/UNC syntax, while retaining full-path ownership checks. Missing modules and mismatched paths produce distinct diagnostics.

`native/file_send.h`, `native/voice_send.h`, and the added `native/local_file_path.h` support extended local drive paths without changing the buffer passed to Win32 I/O. `text_bridge.dll` is rebuilt from these sources; the compatibility self-test DLL is unchanged. `vendor/helper/VENDORED_SHA256.json` records binary hashes and locally modified source hashes.

The local `wxbot_framework/local_paths.py` utility and changes in `files.py` and `voices.py` allow extended Windows drive paths during file-store initialization and media validation. I/O retains the extended prefix for long paths; UNC, device namespaces, URLs, alternate streams, and mapped network drives remain rejected.

`wxbot_framework/snapshot_access.py` adds an inheritable read/traverse ACE for the current process TokenUser to newly created private outbound snapshot directories. File and voice preparation invokes it before writing snapshots, so the same user running unelevated WeChat can read elevated-created snapshots without granting access to other users or write access.

## Python dependencies

The Windows portable package includes the CPython 3.13.9 embeddable runtime.
Its license is included at python/LICENSE.txt.

All Python dependencies retain their own licenses. See the installed package metadata and the `vendor/helper/THIRD_PARTY_NOTICES.md` file.

## Architecture reference

The package boundaries and account-isolation principles in `docs/snowluma-alignment.md`
are inspired by the publicly described architecture of `SnowLuma/SnowLuma`.
No SnowLuma source code is copied into this repository. SnowLuma itself is
source-available under a non-commercial license and is not an OSI open-source
project; its native addons are separate proprietary components.

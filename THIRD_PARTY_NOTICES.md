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

- `wxbot_framework/replies.py` and `native/text_service.py` add bounded XML parsing for inbound appmsg type 57 quote metadata and deliver it before attachment handling. They do not fetch quoted media or add an outbound native quote primitive. The gateway resolves local OneBot reply IDs separately.

- Local documentation corrections in `README.md`, `PRODUCT.md`, `DESIGN.md`, `.impeccable/surfaces/webui-index-html.md`, `docs/development/documentation.md`, `docs/guide/quick-start.md`, and `docs/messages/recall.md` distinguish the standalone upstream application from this gateway, qualify historical design authorization, and correct client profiles, receive verification, recall-plugin controls, and documentation workflow scope. Licenses and copyright notices are preserved. See [integration boundaries](docs/vendor-helper.md).

- `native/bridge_watchdog.py` explicitly adds its native module directory before internal imports so it works with isolated embeddable Python.
- `wxbot_framework/bridge_lifecycle.py` captures watchdog startup output in `logs/bridge_watchdog_startup.log` and reports early exit codes, readiness timeouts, and absolute log paths.

- `native/run_selftest.py` and `native/filehelper_service.py` compare resolved bridge paths consistently across Windows extended drive/UNC syntax, while retaining full-path ownership checks. Missing modules and mismatched paths produce distinct diagnostics.

`native/file_send.h`, `native/voice_send.h`, and the added `native/local_file_path.h` support extended local drive paths without changing the buffer passed to Win32 I/O. `text_bridge.dll` is rebuilt from these sources; the compatibility self-test DLL is unchanged. `vendor/helper/VENDORED_SHA256.json` records binary hashes and locally modified source hashes.

The local `wxbot_framework/local_paths.py` utility and changes in `files.py` and `voices.py` allow extended Windows drive paths during file-store initialization and media validation. I/O retains the extended prefix for long paths; UNC, device namespaces, URLs, alternate streams, and mapped network drives remain rejected.

`wxbot_framework/snapshot_access.py` adds an inheritable read/traverse ACE for the current process TokenUser to newly created private outbound snapshot directories. File and voice preparation invokes it before writing snapshots, so the same user running unelevated WeChat can read elevated-created snapshots without granting access to other users or write access.

`wxbot_framework/sdk.py` joins native mention display labels to text with a space rather than inserting a mandatory newline. Native mention recipient IDs and explicit newlines in the text are preserved.

`wxbot_framework/contacts.py` supplements friend-name lookup with an exact-ID, read-only lookup of the non-friend ContactCache LRU on the verified WeChat 4.1.15.56 build. It reuses the pinned helper's stable LRU reader and documented contact prefix, validates the account and record identity, and rejects records that change during reading. Friend enumeration is unchanged. The upstream [contact reader](https://github.com/Prometheus-first/helper/blob/main/wxbot_framework/contacts.py) also separates friend and non-friend name sources; its newer-version layouts and profiles are not imported here. The pinned upstream commit and DLLs remain unchanged.

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

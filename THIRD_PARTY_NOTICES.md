# Third-Party Notices

## wechatbot-api

`vendor/wechatbot_api` contains an **unmodified** wheel of the independent framework:

- Package: `wechatbot-api 0.1.0.dev15` (import name `wechatbot_api`)
- Build commit: `7157c44f04b40fa51945b42e4d8486709be48422`
- Wheel: `wechatbot_api-0.1.0.dev15-py3-none-win_amd64.whl`, SHA-256 `8dc2845866487138307e1bbf0840e98ae37c868b8a6feb43edee10ccd167b951`
- Native bridge: `wechatbot_api/bin/wca_bridge.dll` (bridge 10 / ABI 1), SHA-256 `5af4026cd8a5b8940c11b451df77ca78b2addaeb64e66e383322449be08a6c0b`
- Target build: WeChat 4.2.0.52 (`Weixin.dll` 204168240 bytes, SHA-256 `d733489eea0b62af77e1aa61ba645cd941583d2a64fb2d7572da818d285f8d84`)
- License: helper Noncommercial License 1.0 (`LicenseRef-helper-noncommercial-1.0`), full text at `vendor/wechatbot_api/LICENSE`

The gateway consumes the wheel only through the public `wechatbot_api` API in `src/wechat_onebot/driver/sdk.py`. It does not patch the wheel or its DLL; upgrades replace the whole wheel and update `vendor/wechatbot_api/VENDORED.json` and this section. The wheel bundles MinHook 1.3.4 and its license files under its own distribution metadata. Pillow is an install-time dependency pinned by `requirements-runtime.lock`.

## helper

`vendor/helper` is vendored from:

- Repository: `https://github.com/Prometheus-first/helper`
- Commit: `119acdf9d74654c6ebb8cded2c6841b85af75867`
- License: helper Noncommercial License 1.0
- License text: `vendor/helper/LICENSE`
- Additional notices: `vendor/helper/THIRD_PARTY_NOTICES.md`

The helper license permits personal learning, research, testing, entertainment, and noncommercial self-use. It does not grant commercial rights. In particular, it does not permit sales, paid bots, paid group services, SaaS, commercial hosting, advertising/marketing use, or integration into commercial products.

The native bridge is built from the helper bridge sources and MinHook 1.3.4. It does not contain WeChat client files.

Local changes relative to the pinned helper commit:

- Local quote parsing additionally accepts direct recordinfo, recorditem/recordxml wrappers and inline referenced XML, with byte/node/depth bounds and categorical diagnostics that exclude message bodies or download credentials. Compact cards with no embedded records remain explicitly unavailable.

- `wxbot_framework/replies.py`, `forwarded.py`, `forwarded_receiver.py` and `native/text_service.py` add bounded inbound quote, link and recursive forwarded-record parsing. Valid quoted/forwarded images use the existing account-bound native downloader with size/digest checks and cancellation cleanup. No generic HTTP fetcher or outbound native quote/forward primitive is added. `docs/messages/forwarded.md` documents these local extensions; the gateway resolves and persists its own OneBot reply/forward IDs.

- Local documentation corrections in `README.md`, `PRODUCT.md`, `DESIGN.md`, `.impeccable/surfaces/webui-index-html.md`, `docs/development/documentation.md`, `docs/guide/quick-start.md`, and `docs/messages/recall.md` distinguish the standalone upstream application from this gateway, qualify historical design authorization, and correct client profiles, receive verification, recall-plugin controls, and documentation workflow scope. Licenses and copyright notices are preserved. See [integration boundaries](docs/vendor-helper.md).

- `native/bridge_watchdog.py` explicitly adds its native module directory before internal imports so it works with isolated embeddable Python.
- `wxbot_framework/bridge_lifecycle.py` captures watchdog startup output in `logs/bridge_watchdog_startup.log` and reports early exit codes, readiness timeouts, and absolute log paths.

- `native/run_selftest.py` and `native/filehelper_service.py` compare resolved bridge paths consistently across Windows extended drive/UNC syntax, while retaining full-path ownership checks. Missing modules and mismatched paths produce distinct diagnostics.

`native/file_send.h`, `native/voice_send.h`, and the added `native/local_file_path.h` support extended local drive paths without changing the buffer passed to Win32 I/O. `text_bridge.dll` is rebuilt from these sources; the compatibility self-test and article DLLs are copied from the pinned upstream commit. `vendor/helper/VENDORED_SHA256.json` records binary hashes and locally modified source hashes.

The local `wxbot_framework/local_paths.py` utility and changes in `files.py` and `voices.py` allow extended Windows drive paths during file-store initialization and media validation. I/O retains the extended prefix for long paths; UNC, device namespaces, URLs, alternate streams, and mapped network drives remain rejected.

`files.prepare_file` also adds the local extended prefix when its private snapshot and the preserved original basename exceed Windows MAX_PATH, and uses the same spelling for bounded cleanup. This retains upstream's full filename preservation without relying on the host's long-path policy. Ordinary-file I/O uses upstream's chunked implementation; gateway input limits continue to apply separately.

`wxbot_framework/snapshot_access.py` adds an inheritable read/traverse ACE for the current process TokenUser to newly created private outbound snapshot directories. File and voice preparation invokes it before writing snapshots, so the same user running unelevated WeChat can read elevated-created snapshots without granting access to other users or write access.

The retained `win_integrity.py` utility and file/image receiver changes prepare owned inbound workspaces for the unelevated WeChat process. `native/text_service.py` downloads images and ordinary files through a fresh path in the bound account's WeChat temp directory, verifies and copies the result into the gateway workspace, then removes its temporary native result. Account and directory checks are retained during the upstream update.

`wxbot_framework/sdk.py` joins native mention display labels to text with a space rather than inserting a mandatory newline. Native mention recipient IDs and explicit newlines in the text are preserved.

`wxbot_framework/contacts.py` retains the verified 4.1.15.56 non-friend LRU reader and combines it with upstream's separately mapped 4.2.0.11 / 4.2.0.12 layouts. Raw names are validated before whitespace trimming; account, exact-ID and stable-read checks remain mandatory. Friend enumeration is unchanged and is unavailable in upstream's 4.2 backend capabilities.

The local rich quote parser runs before attachment handling and retains upstream quote-display metadata without allowing that summary parser to consume rich references early. Pending voice/video monitors are suppressed by the gateway to avoid duplicate placeholder events. Upstream native quote/video/local anti-recall features are not newly exposed as OneBot actions. Local helper tests retain the gateway's mention separator and isolated download fixtures.

The pinned snapshot includes upstream's additional modules and docs solely as helper dependencies and references. This gateway does not start its plugins, article service, scheduled tasks or local anti-recall. `VENDORED_SHA256.json` covers the full distributed snapshot except itself; text files use LF. Binary hashes and provenance also appear in the portable build manifest.

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

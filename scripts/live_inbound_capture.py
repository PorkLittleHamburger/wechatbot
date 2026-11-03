from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from wechat_onebot.config import AppConfig
from wechat_onebot.core.runtime import AccountRuntime


def classify(event: dict[str, Any], flags: dict[str, bool], counts: dict[str, int]) -> None:
    if event.get("post_type") != "message":
        return
    segments = event.get("message") or []
    segment_types = {segment.get("type") for segment in segments if isinstance(segment, dict)}
    if event.get("message_type") == "private":
        if "text" in segment_types:
            flags["text"] = True
            counts["text"] += 1
        if "image" in segment_types:
            flags["image"] = True
            counts["image"] += 1
        if "file" in segment_types:
            flags["file"] = True
            counts["file"] += 1
    elif event.get("message_type") == "group" and event.get("to_me"):
        flags["group_mention"] = True
        counts["group_mention"] += 1


async def run(args: argparse.Namespace) -> int:
    config = AppConfig.load(args.config)
    output = args.output.resolve()
    status_path = args.status.resolve()
    summary_path = args.summary.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = {"text": False, "image": False, "file": False, "group_mention": False}
    counts = {"text": 0, "image": 0, "file": 0, "group_mention": 0}
    all_seen = asyncio.Event()
    runtime = AccountRuntime.create(config)
    stop_capture = False

    def write_status(ready: bool = True, error: str = "") -> None:
        payload = {
            "ready": ready,
            "finished": False,
            "flags": flags,
            "counts": counts,
            "error": error,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def capture(event: dict[str, Any]) -> None:
        nonlocal stop_capture
        classify(event, flags, counts)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        write_status(True)
        if all(flags.values()):
            all_seen.set()

    write_status(False, "starting")
    try:
        await runtime.start()
        runtime.service.set_event_sink(capture)
        write_status(True)
        print(json.dumps({"ready": True, "flags": flags, "counts": counts}, ensure_ascii=False), flush=True)
        try:
            await asyncio.wait_for(all_seen.wait(), timeout=args.duration)
        except asyncio.TimeoutError:
            pass
        finally:
            stop_capture = True
        summary = {
            "finished": True,
            "ok": all(flags.values()),
            "flags": flags,
            "counts": counts,
            "duration_seconds": args.duration,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if summary["ok"] else 2
    except Exception as error:
        write_status(False, str(error))
        summary_path.write_text(json.dumps({"finished": True, "ok": False, "error": str(error)},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), flush=True)
        return 1
    finally:
        await runtime.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--output", type=Path, default=Path("data/live-inbound-events.jsonl"))
    parser.add_argument("--status", type=Path, default=Path("data/live-inbound-status.json"))
    parser.add_argument("--summary", type=Path, default=Path("data/live-inbound-summary.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

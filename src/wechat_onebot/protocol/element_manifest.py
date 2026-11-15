"""Single source of truth for OneBot message-element support in every direction.

The gateway touches message elements in four places:

- ``D`` driver decode: native helper/sdk events become ``models.Segment`` objects
  (``driver/helper.py`` and ``driver/sdk.py``).
- ``S`` segment projection: :func:`protocol.converters._segment_to_onebot`
  renders a ``Segment`` as an OneBot message segment, including stored outgoing
  snapshots.
- ``P`` prepare: :meth:`service.OneBotService._prepare_segments` accepts an
  outbound OneBot segment and turns supported ones into ``PreparedSegment``
  objects.
- ``W`` native send: :meth:`service.OneBotService._send_prepared_message` maps a
  prepared segment onto a ``Driver.send_*`` method.

Values follow the SnowLuma element-manifest vocabulary:

- ``yes``: the gateway has a mapping in this direction.
- ``no``: not implemented here (a recorded gap; adding it needs an explicit
  table update and tests). This includes video send, which is not mapped yet.
- ``by-design-no``: intentionally unsupported because WeChat has no equivalent
  or because the gateway must not fake that semantic.

This table covers gateway mappings only. Build-specific availability (for
example native emoticon send missing on WeChat 4.2 builds) remains in
``Driver.capabilities`` / ``get_capabilities``. Per-segment options can still be
rejected while preparing (flash image, voice changing, QQ ``face.id``,
``at qq=all``); those are validation rules on top of direction support.
"""
from __future__ import annotations

from typing import Literal

ELEMENT_DIRECTIONS = ("D", "S", "P", "W")
SUPPORT_VALUES = ("yes", "no", "by-design-no")

Direction = Literal["D", "S", "P", "W"]
DirectionSupport = Literal["yes", "no", "by-design-no"]


ELEMENT_MANIFEST: dict[str, dict[Direction, DirectionSupport]] = {
    "text": {"D": "yes", "S": "yes", "P": "yes", "W": "yes"},
    "at": {"D": "yes", "S": "yes", "P": "yes", "W": "yes"},
    "image": {"D": "yes", "S": "yes", "P": "yes", "W": "yes"},
    "record": {"D": "yes", "S": "yes", "P": "yes", "W": "yes"},
    "file": {"D": "yes", "S": "yes", "P": "yes", "W": "yes"},
    # Inbound only: helper/sdk report a downloaded video as a ``video`` segment.
    # Outbound video is not mapped to a native send yet.
    "video": {"D": "yes", "S": "yes", "P": "no", "W": "no"},
    # Inbound stickers are normalized to text ("[表情]") or image, never a
    # Segment("face"). Outbound accepts only file-based stickers; QQ face IDs
    # are rejected while preparing.
    "face": {"D": "no", "S": "yes", "P": "yes", "W": "yes"},
    # Inbound quotes arrive in ``raw["reply"]`` and are resolved by the service
    # instead of being a Segment. Native outbound quoting is not mapped yet.
    "reply": {"D": "no", "S": "no", "P": "no", "W": "no"},
    # Inbound forwarded records become an internal ``forward`` segment that is
    # materialized before storage/reporting. Outbound forwarding only runs
    # through send_group_forward_msg/send_private_forward_msg, which expand
    # nodes into ordinary message chains instead of sending a forward segment.
    "forward": {"D": "yes", "S": "yes", "P": "by-design-no", "W": "no"},
    "node": {"D": "no", "S": "no", "P": "by-design-no", "W": "no"},
    # A music segment is accepted for preparation and degraded to voice/text;
    # no music segment ever reaches a native send.
    "music": {"D": "no", "S": "no", "P": "yes", "W": "no"},
    # WeChat has no equivalent of QQ JSON/XML cards, share containers or
    # contact cards, so ordinary send actions reject them by design.
    "json": {"D": "no", "S": "no", "P": "by-design-no", "W": "no"},
    "xml": {"D": "no", "S": "no", "P": "by-design-no", "W": "no"},
    "share": {"D": "no", "S": "no", "P": "by-design-no", "W": "no"},
    "contact": {"D": "no", "S": "no", "P": "by-design-no", "W": "no"},
}

# Prepared segment type -> driver capability name. This is the single place
# where send elements meet the driver capability vocabulary used by
# ``get_capabilities`` and the forward-send capability precheck.
ELEMENT_SEND_CAPABILITIES: dict[str, str] = {
    "text": "text.send",
    "at": "group.text.mention",
    "image": "image.send",
    "file": "file.send",
    "record": "voice.send",
    "face": "emoticon.send",
}

# Segment types a driver can send directly. ``music`` is absent because
# preparation expands it into ``record`` or ``text`` before this stage.
NATIVE_SEND_TYPES = frozenset(
    element for element, support in ELEMENT_MANIFEST.items() if support["W"] == "yes"
)
# Segment types preparation accepts (some expand into other prepared types).
OUTBOUND_SUPPORTED_TYPES = frozenset(
    element for element, support in ELEMENT_MANIFEST.items() if support["P"] == "yes"
)

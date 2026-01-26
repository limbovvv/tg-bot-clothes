from enum import Enum


class GiveawayStatus(str, Enum):
    active = "active"
    closed = "closed"


class EntryStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class BroadcastSegment(str, Enum):
    all_bot_users = "all_bot_users"
    approved_in_active_giveaway = "approved_in_active_giveaway"
    subscribed_verified = "subscribed_verified"


class BroadcastPayloadType(str, Enum):
    text = "text"
    photo = "photo"
    video = "video"
    document = "document"
    video_note = "video_note"

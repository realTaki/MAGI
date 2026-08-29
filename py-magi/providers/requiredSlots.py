"""vNext BUS operations the single provider worker owns."""

from bus import (
    CallLLMJob,
    ChangeProviderJob,
    GetSettingJob,
    RecordTokenUsageJob,
    SlotTag,
)

REQUIRED_SLOTS: tuple[SlotTag, ...] = (
    SlotTag(CallLLMJob, "claim"),
    SlotTag(CallLLMJob, "submit_result"),
    SlotTag(ChangeProviderJob, "claim"),
    SlotTag(ChangeProviderJob, "submit_result"),
    SlotTag(GetSettingJob, "publish"),
    SlotTag(RecordTokenUsageJob, "publish"),
)

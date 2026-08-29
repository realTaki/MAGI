"""vNext BUS operations the single provider worker owns."""

from magi.bus import (
    CallLLMJob,
    ChangeProviderJob,
    GetSettingJob,
    RecordTokenUsageJob,
    Slot,
)

REQUIRED_SLOTS: tuple[Slot, ...] = (
    Slot(CallLLMJob, "claim"),
    Slot(CallLLMJob, "submit_result"),
    Slot(ChangeProviderJob, "claim"),
    Slot(ChangeProviderJob, "submit_result"),
    Slot(GetSettingJob, "publish"),
    Slot(RecordTokenUsageJob, "publish"),
)

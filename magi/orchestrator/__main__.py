"""Run the EVA orchestrator as the dedicated control-plane process."""

import uvicorn

from magi.orchestrator.service import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=42100)

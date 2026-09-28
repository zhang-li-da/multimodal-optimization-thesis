"""Fresh-block E2 2x2 component validation runner."""
from pathlib import Path

from . import study

study.PROTOCOL = Path(__file__).with_name("protocol.r3.final.json")

if __name__ == "__main__":
    study.main()

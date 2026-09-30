"""Low-concurrency execution wrapper for the independently frozen r2 batch."""
from pathlib import Path

from . import study

study.PROTOCOL = Path(__file__).with_name("protocol.r2.final.json")

if __name__ == "__main__":
    study.main()

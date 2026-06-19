"""
Test HarvestPipeline.replace_download_phase() public method.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from magnet_harvester.pipeline import HarvestPipeline


class FakeQbit:
    pass


def test_replace_download_phase_updates_private_field():
    pipeline = HarvestPipeline(
        crawler=None,
        classifier=None,
        qbit=FakeQbit(),
        store=None,
        bus=None,
    )
    new_qbit = FakeQbit()

    pipeline.replace_download_phase(new_qbit)

    assert pipeline._qbit is new_qbit


if __name__ == "__main__":
    test_replace_download_phase_updates_private_field()
    print("=== pipeline replace phase tests passed! ===")

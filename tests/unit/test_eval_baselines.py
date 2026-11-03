import pytest
from evals.cases import CASES
from evals.run_offline import run_baseline


def test_case_set_has_thirty():
    assert len(CASES) == 30
    assert sum(1 for c in CASES if c["injection"]) == 5


@pytest.mark.asyncio
async def test_three_baselines_return_rates():
    for name in ("fixed_workflow", "single_agent", "multi_agent"):
        report = await run_baseline(name)
        assert report["aggregate"]["n"] == 30
        assert "followup_rate" in report["aggregate"]
        assert "citation_exists_rate" in report["aggregate"]
        assert report["notes"]

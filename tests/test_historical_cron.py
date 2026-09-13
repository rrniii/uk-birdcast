import importlib.util
from pathlib import Path

import pytest


def installer():
    path = Path(__file__).parents[1] / "deploy/scripts/install-historical-cron.py"
    spec = importlib.util.spec_from_file_location("historical_cron", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cron_install_preserves_unrelated_jobs_and_is_idempotent():
    module = installer()
    before = "# Existing AVOCET and ICECAPS work\n0 20 * * * unrelated-job\n"
    args = Path("/release/submit-historical-cycle.sh"), Path("/private/cycle.env")
    updated = module.updated_table(before, *args)
    assert before in updated
    assert updated.count("35 * * * *") == 1
    assert "35 * * * * /usr/local/bin/crontamer" in updated
    assert module.updated_table(updated, *args) == updated
    switched = module.updated_table(updated, Path("/new/submit-historical-cycle.sh"), args[1])
    assert before in switched and "/release/" not in switched


def test_cron_install_rejects_broken_or_unmanaged_entries():
    module = installer()
    for previous in (
        module.BEGIN,
        module.END,
        module.END + "\n" + module.BEGIN,
        "0 * * * * /unknown/submit-historical-cycle.sh",
    ):
        with pytest.raises(ValueError):
            module.updated_table(previous, Path("/release/submit.sh"), Path("/private/cycle.env"))


def test_parallel_worker_uses_the_account_authorized_parallel_qos():
    worker = (
        Path(__file__).parents[1] / "deploy/slurm/birdcast-uk-historical-cycle.sbatch"
    ).read_text()
    assert "#SBATCH --qos=high" in worker
    assert "#SBATCH --cpus-per-task=8" in worker
    assert "#SBATCH --mem=24G" in worker
    assert "#SBATCH --time=12:00:00" in worker


def test_model_cron_preserves_observation_and_unrelated_jobs():
    module = installer()
    observation = module.updated_table(
        "0 20 * * * upstream-avocet\n",
        Path("/release/submit-historical-cycle.sh"),
        Path("/private/h.env"),
    )
    args = Path("/release/submit-model-cycle.sh"), Path("/private/m.env"), "model"
    updated = module.updated_table(observation, *args)
    assert observation in updated
    assert updated.count("20 * * * *") == 1
    assert updated.count("35 * * * *") == 1
    assert module.updated_table(updated, *args) == updated
    assert "forecast" not in updated and "ecmwf" not in updated

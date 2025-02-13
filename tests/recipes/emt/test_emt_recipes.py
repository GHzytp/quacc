import os
from shutil import rmtree

import pytest
from ase.build import bulk
from jobflow.managers.local import run_locally

from quacc.recipes.emt.core import RelaxJob, StaticJob


def teardown_module():
    for f in os.listdir(os.getcwd()):
        if (
            f.endswith(".log")
            or f.endswith(".pckl")
            or f.endswith(".traj")
            or f.endswith(".out")
        ):
            os.remove(f)
        if "quacc-tmp" in f or f == "tmp_dir":
            rmtree(f)


def test_static_Job():
    atoms = bulk("Cu") * (2, 2, 2)
    atoms[0].position += [0.1, 0.1, 0.1]

    job = StaticJob().make(atoms)
    responses = run_locally(job, ensure_success=True)
    output = responses[job.uuid][1].output
    assert output["nsites"] == len(atoms)
    assert output["parameters"]["asap_cutoff"] == False
    assert output["name"] == "EMT-Static"
    assert output["results"]["energy"] == pytest.approx(0.07001766638245854)

    job = StaticJob(asap_cutoff=True).make(atoms)
    responses = run_locally(job, ensure_success=True)
    output = responses[job.uuid][1].output
    assert output["nsites"] == len(atoms)
    assert output["parameters"]["asap_cutoff"] == True
    assert output["name"] == "EMT-Static"
    assert output["results"]["energy"] == pytest.approx(0.11074520235398744)


def test_relax_Job():
    atoms = bulk("Cu") * (2, 2, 2)
    atoms[0].position += [0.1, 0.1, 0.1]

    job = RelaxJob().make(atoms)
    responses = run_locally(job, ensure_success=True)
    output = responses[job.uuid][1].output
    assert output["nsites"] == len(atoms)
    assert output["parameters"]["asap_cutoff"] == False
    assert output["name"] == "EMT-Relax"
    assert output["results"]["energy"] == pytest.approx(-0.04517048198212592)

    job = RelaxJob(asap_cutoff=True).make(atoms)
    responses = run_locally(job, ensure_success=True)
    output = responses[job.uuid][1].output
    assert output["nsites"] == len(atoms)
    assert output["parameters"]["asap_cutoff"] == True
    assert output["name"] == "EMT-Relax"
    assert output["results"]["energy"] == pytest.approx(-0.004527567070971017)

    job = RelaxJob(fmax=0.01).make(atoms)
    responses = run_locally(job, ensure_success=True)
    output = responses[job.uuid][1].output
    assert output["nsites"] == len(atoms)
    assert output["name"] == "EMT-Relax"
    assert output["results"]["energy"] == pytest.approx(-0.0454470914411953)

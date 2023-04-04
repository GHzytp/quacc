"""
Core recipes for the tblite code
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from ase.atoms import Atoms
from ase.optimize import FIRE
from ase.optimize.optimize import Optimizer
from jobflow import Maker, job
from monty.dev import requires

from quacc.schemas.calc import summarize_run
from quacc.util.calc import run_ase_opt, run_calc

try:
    from tblite.ase import TBLite
except ImportError:
    TBLite = None

@dataclass
class StaticJob(Maker):
    """
    Class to carry out a single-point calculation.

    Parameters
    ----------
    name
        Name of the job.
    method
        GFN0-xTB, GFN1-xTB, GFN2-xTB.
    tblite_kwargs
        Dictionary of custom kwargs for the tblite calculator.
    """

    name: str = "tblite-Static"
    method: str = "GFN2-xTB"
    tblite_kwargs: Dict[str, Any] = field(default_factory=dict)

    @job
    @requires(
        TBLite,
        "tblite must be installed. Try conda install -c conda-forge tblite",
    )
    def make(self, atoms: Atoms) -> Dict[str, Any]:
        """
        Make the run.

        Parameters
        ----------
        atoms
            .Atoms object`

        Returns
        -------
        Dict
            Summary of the run.
        """
        atoms.calc = TBLite(method=self.method, **self.tblite_kwargs)
        new_atoms = run_calc(atoms)
        summary = summarize_run(
            new_atoms, input_atoms=atoms, additional_fields={"name": self.name}
        )

        return summary


@dataclass
class RelaxJob(Maker):
    """
    Class to relax a structure.

    Parameters
    ----------
    name
        Name of the job.
    method
        GFN0-xTB, GFN1-xTB, GFN2-xTB.
    tblite_kwargs
        Dictionary of custom kwargs for the tblite calculator.
    fmax
        Tolerance for the force convergence (in eV/A).
    optimizer
        .Optimizer class to use for the relaxation.
    opt_kwargs
        Dictionary of kwargs for the optimizer.
    """

    name: str = "tblite-Relax"
    method: str = "GFN2-xTB"
    tblite_kwargs: Dict[str, Any] = field(default_factory=dict)
    fmax: float = 0.01
    optimizer: Optimizer = FIRE
    opt_kwargs: Dict[str, Any] = field(default_factory=dict)

    @job
    @requires(
        TBLite,
        "tblite must be installed. Try conda install -c conda-forge tblite",
    )
    def make(self, atoms: Atoms) -> Dict[str, Any]:
        """
        Make the run.

        Parameters
        ----------
        atoms
            .Atoms object

        Returns
        -------
        Dict
            Summary of the run.
        """
        atoms.calc = TBLite(method=self.method, **self.tblite_kwargs)
        new_atoms = run_ase_opt(
            atoms, fmax=self.fmax, optimizer=self.optimizer, opt_kwargs=self.opt_kwargs
        )
        summary = summarize_run(
            new_atoms, input_atoms=atoms, additional_fields={"name": self.name}
        )

        return summary

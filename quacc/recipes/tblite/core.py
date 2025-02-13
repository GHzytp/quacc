"""
Core recipes for the tblite code
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from ase.atoms import Atoms
from jobflow import Maker, job
from monty.dev import requires

from quacc.schemas.calc import summarize_opt_run, summarize_run
from quacc.util.calc import ideal_gas_thermo, run_ase_opt, run_ase_vib, run_calc

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
        GFN1-xTB, GFN2-xTB, and IPEA1-xTB.
    tblite_kwargs
        Dictionary of custom kwargs for the tblite calculator.
    """

    name: str = "tblite-Static"
    method: str = "GFN2-xTB"
    tblite_kwargs: Dict[str, Any] = field(default_factory=dict)

    @job
    @requires(
        TBLite,
        "tblite must be installed. Try pip install tblite[ase]",
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
    fmax
        Tolerance for the force convergence (in eV/A).
    max_steps
        Maximum number of steps to take.
    optimizer
        .Optimizer class to use for the relaxation.
    tblite_kwargs
        Dictionary of custom kwargs for the tblite calculator.
    opt_kwargs
        Dictionary of kwargs for the optimizer.
    """

    name: str = "tblite-Relax"
    method: str = "GFN2-xTB"
    fmax: float = 0.01
    max_steps: int = 1000
    optimizer: str = "FIRE"
    tblite_kwargs: Dict[str, Any] = field(default_factory=dict)
    opt_kwargs: Dict[str, Any] = field(default_factory=dict)

    @job
    @requires(
        TBLite,
        "tblite must be installed. Try pip install tblite[ase]",
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
        traj = run_ase_opt(
            atoms,
            fmax=self.fmax,
            max_steps=self.max_steps,
            optimizer=self.optimizer,
            opt_kwargs=self.opt_kwargs,
        )
        summary = summarize_opt_run(
            traj, atoms.calc.parameters, additional_fields={"name": self.name}
        )

        return summary


@dataclass
class ThermoJob(Maker):
    """
    Class to run a frequency job and calculate thermochemistry.

    Parameters
    ----------
    name
        Name of the job.
    method
        GFN0-xTB, GFN1-xTB, GFN2-xTB, GFN-FF.
    temperature
        Temperature in Kelvins.
    pressure
        Pressure in bar.
    xtb_kwargs
        Dictionary of custom kwargs for the xTB calculator.
    """

    name: str = "xTB-Freq"
    method: str = "GFN2-xTB"
    temperature: float = 298.15
    pressure: float = 1.0
    xtb_kwargs: Dict[str, Any] = field(default_factory=dict)

    @job
    @requires(TBLite, "tblite must be installed. Try pip install tblite[ase]")
    def make(self, atoms: Atoms, energy: float = 0.0) -> Dict[str, Any]:
        """
        Make the run.

        Parameters
        ----------
        atoms
            .Atoms object
        energy
            Potential energy in eV. If 0, then the output is just the correction.

        Returns
        -------
        Dict
            Summary of the thermochemistry.
        """
        atoms.calc = TBLite(method=self.method, **self.xtb_kwargs)
        vibrations = run_ase_vib(atoms)
        thermo_summary = ideal_gas_thermo(
            atoms,
            vibrations.get_frequencies(),
            temperature=self.temperature,
            pressure=self.pressure,
            energy=energy,
        )
        thermo_summary["name"] = self.name

        return thermo_summary

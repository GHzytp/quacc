"""
A wrapper around ASE's Vasp calculator that makes it better suited for high-throughput DFT.
"""
from __future__ import annotations

import inspect
import os
import warnings
from typing import Any, Dict, List, Tuple

import numpy as np
from ase.atoms import Atoms
from ase.calculators.vasp import Vasp as Vasp_
from ase.calculators.vasp.setups import _setups_defaults as ase_default_setups
from ase.constraints import FixAtoms

from quacc import SETTINGS
from quacc.custodian import vasp as custodian_vasp
from quacc.presets import vasp as vasp_defaults
from quacc.util.atoms import check_is_metal, get_highest_block, set_magmoms
from quacc.util.basics import load_yaml_calc
from quacc.util.calc import _convert_auto_kpts

DEFAULT_CALCS_DIR = os.path.dirname(vasp_defaults.__file__)


class Vasp(Vasp_):
    """
    This is a wrapper around the Vasp calculator that adjusts INCAR parameters on-the-fly,
    allows for ASE to run VASP via Custodian, and supports several automatic k-point generation schemes
    from Pymatgen.

    Parameters
    ----------
    atoms
        The Atoms object to be used for the calculation.
    preset
        The path to a .yaml file containing a list of INCAR parameters to use as a "preset"
        for the calculator. If no filepath is present, it will look in quacc/defaults/calcs/vasp, such
        that preset="BulkRelaxSet" is supported. It will append .yaml at the end if not present.
        Note that any specific kwargs take precedence over the flags set in the preset dictionary.
    custodian
        Whether to use Custodian to run VASP.
        Default is True in settings.
    incar_copilot
        If True, the INCAR parameters will be adjusted if they go against the VASP manual.
        Default is True in settings.
    copy_magmoms
        If True, any pre-existing atoms.get_magnetic_moments() will be set in atoms.set_initial_magnetic_moments().
        Set this to False if you want to use a preset's magnetic moments every time.
    preset_mag_default
        Default magmom value for sites without one explicitly specified in the preset. Only used if a preset is
        specified with an elemental_mags_dict key-value pair.
        Default is 1.0 in settings.
    mag_cutoff
        Set all initial magmoms to 0 if all have a magnitude below this value.
        Default is 0.05 in settings.
    verbose
        If True, warnings will be raised when INCAR parameters are changed.
    **kwargs
        Additional arguments to be passed to the VASP calculator, e.g. xc='PBE', encut=520. Takes all valid
        ASE calculator arguments, in addition to those custom to QuAcc.

    Returns
    -------
    Atoms
        The ASE Atoms object with attached VASP calculator.
    """

    def __init__(
        self,
        atoms: Atoms,
        preset: None | str = None,
        custodian: bool = SETTINGS.VASP_CUSTODIAN,
        incar_copilot: bool = SETTINGS.VASP_INCAR_COPILOT,
        copy_magmoms: bool = SETTINGS.VASP_COPY_MAGMOMS,
        preset_mag_default: float = SETTINGS.VASP_PRESET_MAG_DEFAULT,
        mag_cutoff: None | float = SETTINGS.VASP_MAG_CUTOFF,
        verbose: bool = SETTINGS.VASP_VERBOSE,
        **kwargs,
    ):
        # Check constraints
        if (
            custodian
            and atoms.constraints
            and not all(isinstance(c, FixAtoms) for c in atoms.constraints)
        ):
            raise ValueError(
                "Atoms object has a constraint that is not compatible with Custodian"
            )

        # Get VASP executable command, if necessary, and specify child environment
        # variables
        command = _manage_environment(custodian)

        # Get user-defined preset parameters for the calculator
        if preset:
            calc_preset = load_yaml_calc(os.path.join(DEFAULT_CALCS_DIR, preset))[
                "inputs"
            ]
        else:
            calc_preset = {}

        # Collect all the calculator parameters and prioritize the kwargs
        # in the case of duplicates.
        user_calc_params = {**calc_preset, **kwargs}
        none_keys = [k for k, v in user_calc_params.items() if v is None]
        for none_key in none_keys:
            del user_calc_params[none_key]

        # Allow the user to use setups='mysetups.yaml' to load in a custom setups
        # from a YAML file
        if (
            isinstance(user_calc_params.get("setups"), str)
            and user_calc_params["setups"] not in ase_default_setups
        ):
            user_calc_params["setups"] = load_yaml_calc(
                os.path.join(DEFAULT_CALCS_DIR, user_calc_params["setups"])
            )["inputs"]["setups"]

        # If the preset has auto_kpts but the user explicitly requests kpts, then
        # we should honor that.
        if kwargs.get("kpts") and calc_preset.get("auto_kpts"):
            del user_calc_params["auto_kpts"]

        # Handle special arguments in the user calc parameters that
        # ASE does not natively support
        if user_calc_params.get("elemental_magmoms"):
            elemental_mags_dict = user_calc_params["elemental_magmoms"]
        else:
            elemental_mags_dict = None
        if user_calc_params.get("auto_kpts"):
            auto_kpts = user_calc_params["auto_kpts"]
        else:
            auto_kpts = None
        if user_calc_params.get("auto_dipole"):
            auto_dipole = user_calc_params["auto_dipole"]
        else:
            auto_dipole = None
        user_calc_params.pop("elemental_magmoms", None)
        user_calc_params.pop("auto_kpts", None)
        user_calc_params.pop("auto_dipole", None)

        # Make automatic k-point mesh
        if auto_kpts:
            kpts, gamma, reciprocal = _convert_auto_kpts(atoms, auto_kpts)
            user_calc_params["kpts"] = kpts
            if reciprocal and user_calc_params.get("reciprocal") is None:
                user_calc_params["reciprocal"] = reciprocal
            if user_calc_params.get("gamma") is None:
                user_calc_params["gamma"] = gamma

        # Add dipole corrections if requested
        if auto_dipole:
            com = atoms.get_center_of_mass(scaled=True)
            if "dipol" not in user_calc_params:
                user_calc_params["dipol"] = com
            if "idipol" not in user_calc_params:
                user_calc_params["idipol"] = 3
            if "ldipol" not in user_calc_params:
                user_calc_params["ldipol"] = True

        # Set magnetic moments
        set_magmoms(
            atoms,
            elemental_mags_dict=elemental_mags_dict,
            copy_magmoms=copy_magmoms,
            elemental_mags_default=preset_mag_default,
            mag_cutoff=mag_cutoff,
        )

        # Handle INCAR swaps as needed
        if incar_copilot:
            user_calc_params = _calc_swaps(
                atoms, user_calc_params, auto_kpts=auto_kpts, verbose=verbose
            )

        # Remove unused INCAR flags
        user_calc_params = _remove_unused_flags(user_calc_params)

        # Instantiate the calculator!
        super().__init__(atoms=atoms, command=command, **user_calc_params)


def _manage_environment(custodian: bool = True) -> str:
    """
    Manage the environment for the VASP calculator.

    Parameters
    ----------
    custodian
        If True, Custodian will be used to run VASP.

    Returns
    -------
    str
        The command flag to pass to the Vasp calculator.
    """

    # Check ASE environment variables
    if "VASP_PP_PATH" not in os.environ:
        warnings.warn(
            "The VASP_PP_PATH environment variable must point to the library of VASP pseudopotentials. See the ASE Vasp calculator documentation for details.",
        )

    # Check if Custodian should be used and confirm environment variables are set
    if custodian:
        # Return the command flag
        run_vasp_custodian_file = os.path.abspath(inspect.getfile(custodian_vasp))
        command = f"python {run_vasp_custodian_file}"
    else:
        if "ASE_VASP_COMMAND" not in os.environ and "VASP_SCRIPT" not in os.environ:
            warnings.warn(
                "ASE_VASP_COMMAND or VASP_SCRIPT must be set in the environment to run VASP. See the ASE Vasp calculator documentation for details."
            )
        command = None

    return command


def _remove_unused_flags(user_calc_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Removes unused flags in the INCAR, like EDIFFG if you are doing NSW = 0.

    Parameters
    ----------
    user_calc_params
        User-specified calculation parameters

    Returns
    -------
    Dict
        Adjusted user-specified calculation parameters
    """

    # Turn off opt flags if NSW = 0
    opt_flags = ("ediffg", "ibrion", "isif", "potim", "iopt")
    if user_calc_params.get("nsw", 0) == 0:
        for opt_flag in opt_flags:
            user_calc_params.pop(opt_flag, None)

    # Turn off +U flags if +U is not even used
    ldau_flags = (
        "ldau",
        "ldauu",
        "ldauj",
        "ldaul",
        "ldautype",
        "ldauprint",
        "ldau_luj",
    )
    if not user_calc_params.get("ldau", False) and not user_calc_params.get(
        "ldau_luj", None
    ):
        for ldau_flag in ldau_flags:
            user_calc_params.pop(ldau_flag, None)

    return user_calc_params


def _calc_swaps(
    atoms: Atoms,
    user_calc_params: Dict[str, Any],
    auto_kpts: None
    | Dict[str, float]
    | Dict[str, List[Tuple[float, float]]]
    | Dict[str, List[Tuple[float, float, float]]],
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Swaps out bad INCAR flags.

    Parameters
    ----------
    .Atoms
        Atoms object
    user_calc_params
        User-specified calculation parameters
    auto_kpts
        The automatic k-point scheme dictionary
    verbose
        Whether to print out any time the input flags are adjusted.

    Returns
    -------
    Dict[str,Any]
        Dictionary of new user-specified calculation parameters
    """
    is_metal = check_is_metal(atoms)
    max_block = get_highest_block(atoms)
    calc = Vasp_(**user_calc_params)

    if (
        not calc.int_params["lmaxmix"] or calc.int_params["lmaxmix"] < 6
    ) and max_block == "f":
        if verbose:
            warnings.warn("Copilot: Setting LMAXMIX = 6 because you have an f-element.")
        calc.set(lmaxmix=6)
    elif (
        not calc.int_params["lmaxmix"] or calc.int_params["lmaxmix"] < 4
    ) and max_block == "d":
        if verbose:
            warnings.warn("Copilot: Setting LMAXMIX = 4 because you have a d-element")
        calc.set(lmaxmix=4)

    if (
        calc.bool_params["luse_vdw"]
        or calc.bool_params["lhfcalc"]
        or calc.bool_params["ldau"]
        or calc.dict_params["ldau_luj"]
        or calc.string_params["metagga"]
    ) and not calc.bool_params["lasph"]:
        if verbose:
            warnings.warn(
                "Copilot: Setting LASPH = True because you have a +U, vdW, meta-GGA, or hybrid calculation."
            )
        calc.set(lasph=True)

    if (
        calc.bool_params["lasph"]
        and (not calc.int_params["lmaxtau"] or calc.int_params["lmaxtau"] < 8)
        and max_block == "f"
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting LMAXTAU = 8 because you have LASPH = True and an f-element."
            )
        calc.set(lmaxtau=8)

    if calc.string_params["metagga"] and (
        not calc.string_params["algo"] or calc.string_params["algo"].lower() != "all"
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting ALGO = All because you have a meta-GGA calculation."
            )
        calc.set(algo="all")

    if calc.bool_params["lhfcalc"] and (
        not calc.string_params["algo"]
        or calc.string_params["algo"].lower() not in ["all", "damped"]
    ):
        if is_metal:
            calc.set(algo="damped", time=0.5)
            if verbose:
                warnings.warn(
                    "Copilot: Setting ALGO = Damped, TIME = 0.5 because you have a hybrid calculation with a metal."
                )
        else:
            calc.set(algo="all")
            if verbose:
                warnings.warn(
                    "Copilot: Setting ALGO = All because you have a hybrid calculation."
                )

    if (
        is_metal
        and (calc.int_params["ismear"] and calc.int_params["ismear"] < 0)
        and (calc.int_params["nsw"] and calc.int_params["nsw"] > 0)
    ):
        if verbose:
            warnings.warn(
                "Copilot: You are relaxing a likely metal. Setting ISMEAR = 1 and SIGMA = 0.1."
            )
        calc.set(ismear=1, sigma=0.1)

    if (
        calc.int_params["nedos"]
        and calc.int_params["ismear"] != -5
        and calc.int_params["nsw"] in (None, 0)
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting ISMEAR = -5 because you have a static DOS calculation."
            )
        calc.set(ismear=-5)

    if (
        calc.int_params["ismear"] == -5
        and np.product(calc.kpts) < 4
        and calc.float_params["kspacing"] is None
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting ISMEAR = 0 because you don't have enough k-points for ISMEAR = -5."
            )
        calc.set(ismear=0)

    if (
        auto_kpts
        and auto_kpts.get("line_density", None)
        and calc.int_params["ismear"] != 0
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting ISMEAR = 0 and SIGMA = 0.01 because you are doing a line mode calculation."
            )
        calc.set(ismear=0, sigma=0.01)

    if calc.int_params["ismear"] == -5 and (
        not calc.float_params["sigma"] or calc.float_params["sigma"] > 0.05
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting SIGMA = 0.05 because ISMEAR = -5 was requested with SIGMA > 0.05."
            )
        calc.set(sigma=0.05)

    if (
        calc.float_params["kspacing"]
        and (calc.float_params["kspacing"] and calc.float_params["kspacing"] > 0.5)
        and calc.int_params["ismear"] == -5
    ):
        if verbose:
            warnings.warn(
                "Copilot: KSPACING is likely too large for ISMEAR = -5. Setting ISMEAR = 0."
            )
        calc.set(ismear=0)

    if (
        calc.int_params["nsw"]
        and calc.int_params["nsw"] > 0
        and calc.bool_params["laechg"]
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting LAECHG = False because you have NSW > 0. LAECHG is not compatible with NSW > 0."
            )
        calc.set(laechg=False)

    if calc.int_params["ldauprint"] in (None, 0) and (
        calc.bool_params["ldau"] or calc.dict_params["ldau_luj"]
    ):
        if verbose:
            warnings.warn("Copilot: Setting LDAUPRINT = 1 because LDAU = True.")
        calc.set(ldauprint=1)

    if calc.special_params["lreal"] and calc.int_params["nsw"] in (None, 0, 1):
        if verbose:
            warnings.warn(
                "Copilot: Setting LREAL = False because you are running a static calculation. LREAL != False can be bad for energies."
            )
        calc.set(lreal=False)

    if not calc.int_params["lorbit"] and (
        calc.int_params["ispin"] == 2
        or np.any(atoms.get_initial_magnetic_moments() != 0)
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting LORBIT = 11 because you have a spin-polarized calculation."
            )
        calc.set(lorbit=11)

    if (
        (calc.int_params["ncore"] and calc.int_params["ncore"] > 1)
        or (calc.int_params["npar"] and calc.int_params["npar"] > 1)
    ) and (
        calc.bool_params["lhfcalc"] is True
        or calc.bool_params["lrpa"] is True
        or calc.bool_params["lepsilon"] is True
        or calc.int_params["ibrion"] in [5, 6, 7, 8]
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting NCORE = 1 because NCORE/NPAR is not compatible with this job type."
            )
        calc.set(ncore=1)
        calc.set(npar=None)

    if (
        (calc.int_params["ncore"] and calc.int_params["ncore"] > 1)
        or (calc.int_params["npar"] and calc.int_params["npar"] > 1)
    ) and len(atoms) <= 4:
        if verbose:
            warnings.warn(
                "Copilot: Setting NCORE = 1 because you have a very small structure."
            )
        calc.set(ncore=1)
        calc.set(npar=None)

    if (
        calc.int_params["kpar"]
        and calc.int_params["kpar"] > np.prod(calc.kpts)
        and calc.float_params["kspacing"] is None
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting KPAR = 1 because you have too few k-points to parallelize."
            )
        calc.set(kpar=1)

    if (
        calc.int_params["nsw"]
        and calc.int_params["nsw"] > 0
        and calc.int_params["isym"]
        and calc.int_params["isym"] > 0
    ):
        if verbose:
            warnings.warn(
                "Copilot: Setting ISYM = 0 because you are running a relaxation."
            )
        calc.set(isym=0)

    if calc.bool_params["lhfcalc"] is True and calc.int_params["isym"] in (1, 2):
        if verbose:
            warnings.warn(
                "Copilot: Setting ISYM = 3 because you are running a hybrid calculation."
            )
        calc.set(isym=3)

    if calc.bool_params["lsorbit"]:
        if verbose:
            warnings.warn(
                "Copilot: Setting ISYM = -1 because you are running a SOC calculation."
            )
        calc.set(isym=-1)

    if calc.bool_params["luse_vdw"] and "ASE_VASP_VDW" not in os.environ:
        warnings.warn("ASE_VASP_VDW was not set, yet you requested a vdW functional.")

    return calc.parameters

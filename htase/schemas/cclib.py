import cclib
import os
from monty.json import jsanitize
from htase.schemas.atoms import atoms_to_db
from htase.util.atoms import prep_next_run as prep_next_run_


def results_to_db(atoms, output_file, prep_next_run=True):
    """
    Get tabulated results from a molecular DFT run and store them in a database-friendly format.
    This is meant to be a general parser built on top of cclib.

    Args:
        atoms (ase.Atoms): ASE Atoms object following a calculation.
        output_file (str): Path to the main output file.
        prep_next_run (bool): Whether the Atoms object storeed in {"atoms": atoms} should be prepared
            for the next run. This clears out any attached calculator and moves the final magmoms to the
            initial magmoms.
            Defauls to True.

    Returns:
        results (dict): dictionary of tabulated results
    """
    # Make sure there is a calculator with results
    if not atoms.calc:
        raise ValueError("ASE Atoms object has no attached calculator.")
    if not atoms.calc.results:
        raise ValueError("ASE Atoms object's calculator has no results.")

    # Fetch all tabulated results from the attached calculator
    if not os.path.exists(output_file):
        raise FileNotFoundError(f"Could not find {output_file}")
    outputs = cclib.io.ccopen(output_file)
    if outputs is None:
        raise ValueError(f"cclib could not parse {output_file}")
    outputs = outputs.parse()
    outputs_dict = outputs.getattributes()
    results = {"output": outputs_dict}
    metadata = outputs.metadata

    # Get the calculator inputs
    inputs = atoms.calc.parameters

    # Prepares the Atoms object for the next run by moving the
    # final magmoms to initial, clearing the calculator state,
    # and assigning the resulting Atoms object a unique ID.
    if prep_next_run:
        atoms = prep_next_run_(atoms)

    # Get tabulated properties of the structure itself
    atoms_db = atoms_to_db(atoms)

    # Create a dictionary of the inputs/outputs
    results_full = {**atoms_db, **inputs, **metadata, **results}

    # Make sure it's all JSON serializable
    results_full = jsanitize(results_full)

    return results_full

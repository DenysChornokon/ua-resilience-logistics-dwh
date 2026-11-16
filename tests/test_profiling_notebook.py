from pathlib import Path

import nbformat


def test_profiling_notebook_is_valid_and_contains_executable_cells():
    path = Path(__file__).resolve().parents[1] / 'notebooks' / '01_data_profiling.ipynb'
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)
    assert any(cell.cell_type == 'code' and cell.source.strip() for cell in notebook.cells)

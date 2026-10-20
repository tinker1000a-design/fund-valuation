"""ASCII-named launcher so Windows batch files can start the Unicode script."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("基金追踪站.py")), run_name="__main__")

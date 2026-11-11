import os, sys, tempfile
from pathlib import Path

# Tests never touch the real ~/.model-desk: point the kit at a throwaway folder before it is imported.
os.environ["MODEL_DESK_HOME"] = tempfile.mkdtemp(prefix="model-desk-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

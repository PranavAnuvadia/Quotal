import sys
import os

project_dir = os.path.dirname(os.path.abspath(__file__))
venv_site = os.path.join(project_dir, ".venv", "Lib", "site-packages")
if os.path.exists(venv_site) and venv_site not in sys.path:
    sys.path.insert(0, venv_site)

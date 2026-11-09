import sys
from pathlib import Path

# The app runs from app/ (gunicorn app:app), so import modules the same way.
sys.path.insert(0, str(Path(__file__).parent.parent))
# Repo root, for cross-checks against counter/ config.
sys.path.insert(1, str(Path(__file__).parent.parent.parent))

"""Open the project-owned CNKI browser for manual access/login, never automate authentication."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pharm.literature.browser_session import open_session

if __name__ == "__main__":
    print(json.dumps(open_session(), ensure_ascii=False, indent=2))

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from path_manager import PathManagerApp


if __name__ == "__main__":
    PathManagerApp().run()

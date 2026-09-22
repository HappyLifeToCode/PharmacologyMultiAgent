"""Build against an existing Cytoscape installation; no downloaded dependencies."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cytoscape-home", type=Path, required=True)
    parser.add_argument("--jdk-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    api = list(args.cytoscape_home.glob("framework/system/org/cytoscape/api-bundle/*/*.jar"))
    osgi = list(args.cytoscape_home.glob("framework/lib/boot/osgi.core-*.jar"))
    if len(api) != 1 or len(osgi) != 1:
        parser.error("Expected one api-bundle and one osgi.core JAR")
    extension = ".exe" if os.name == "nt" else ""
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source = root / "integrations/cytonca_bridge"
    with tempfile.TemporaryDirectory() as classes:
        subprocess.run([str(args.jdk_home / "bin" / ("javac" + extension)), "--release", "11",
                        "-cp", os.pathsep.join(map(str, api + osgi)), "-d", classes,
                        str(source / "src/pharm/bridge/Activator.java")], check=True)
        subprocess.run([str(args.jdk_home / "bin" / ("jar" + extension)), "cfm", str(args.output),
                        str(source / "MANIFEST.MF"), "-C", classes, "."], check=True)
    print(args.output.resolve())


if __name__ == "__main__":
    main()

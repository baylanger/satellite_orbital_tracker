import json
import os

def main():
    reqs_path = "requirements.txt"
    manifest_path = "custom_components/satellite_orbital_tracker/manifest.json"

    if not os.path.exists(reqs_path) or not os.path.exists(manifest_path):
        print("Requirements or manifest file missing.")
        return

    # Read requirements.txt, ignoring comments and empty lines
    requirements = []
    with open(reqs_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                requirements.append(line)

    # Load manifest.json
    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    # Update requirements array
    manifest["requirements"] = requirements

    # Write back with nice formatting
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    
    print(f"Successfully updated manifest requirements to: {requirements}")

if __name__ == "__main__":
    main()

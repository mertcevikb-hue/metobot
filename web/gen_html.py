import pathlib

out = pathlib.Path(__file__).parent / "static" / "index.html"

if __name__ == "__main__":
    if out.exists():
        print(f"Metobot UI is active at {out} ({out.stat().st_size} bytes).")
    else:
        print(f"Warning: {out} does not exist.")

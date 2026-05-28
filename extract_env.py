"""Extract TOKEN from HAR file for chat.z.ai proxy."""
import json, sys

def extract(har_path, output_path=None):
    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)

    entries = har.get("log", {}).get("entries", [])
    token = ""
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if "/api/v2/chat/completions" in url:
            for q in entry["request"].get("queryString", []):
                if q["name"] == "token":
                    token = q["value"]
            break

    lines = [
        "# chat.z.ai Proxy Config",
        f"TOKEN={token}",
        "",
        "# Server",
        "HOST=0.0.0.0",
        "PORT=8000",
        "MODEL_NAME=GLM-5.1",
        "DSML_ENABLED=true",
        "",
    ]

    text = "\n".join(lines)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Written to {output_path}")
    else:
        print(text)

    print(f"\nToken: {token[:30]}...")
    return True


if __name__ == "__main__":
    har = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    extract(har, out)

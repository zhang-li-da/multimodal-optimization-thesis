"""Print only nonsensitive provider metadata; never print credentials."""
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def jsonc_load(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    output = []
    i, quoted, escaped = 0, False, False
    while i < len(text):
        ch = text[i]
        if quoted:
            output.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
            i += 1
        elif ch == '"':
            quoted = True
            output.append(ch)
            i += 1
        elif text[i:i+2] == "//":
            end = text.find("\n", i)
            i = len(text) if end == -1 else end
        elif text[i:i+2] == "/*":
            end = text.find("*/", i+2)
            if end == -1:
                raise ValueError("unterminated comment")
            i = end + 2
        else:
            output.append(ch)
            i += 1
    text = "".join(output)
    # Remove trailing commas outside quoted strings.
    output, quoted, escaped = [], False, False
    for i, ch in enumerate(text):
        if not quoted and ch == ",":
            j = i+1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "}]":
                continue
        output.append(ch)
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
    return json.loads("".join(output))


def main():
    user = Path.home()
    config_path = user / ".config/opencode/opencode.jsonc"
    auth_path = user / ".local/share/opencode/auth.json"
    cfg = jsonc_load(config_path) if config_path.exists() else {}
    auth = jsonc_load(auth_path) if auth_path.exists() else {}
    providers = []
    for name, entry in cfg.get("provider", {}).items():
        options = entry.get("options", {})
        providers.append({
            "provider": name, "npm": entry.get("npm"),
            "base_url_host": urlsplit(options.get("baseURL", "")).hostname,
            "base_url_path": urlsplit(options.get("baseURL", "")).path,
            "models": list(entry.get("models", {})),
            "has_config_key": bool(options.get("apiKey")),
            "auth_type": auth.get(name, {}).get("type"),
            "has_auth": name in auth,
            "option_keys": list(options),
        })
    print(json.dumps({"default_model": cfg.get("model"),
        "providers": providers,
        "auth_providers": {k: v.get("type") for k,v in auth.items()},
        "cpu_count": os.cpu_count(),
        "modules": {m: bool(importlib.util.find_spec(m)) for m in
            ["numpy", "scipy", "sklearn", "pytest", "matplotlib", "requests", "httpx", "playwright", "psutil"]},
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

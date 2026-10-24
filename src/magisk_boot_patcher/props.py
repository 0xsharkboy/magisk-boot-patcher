def parse_props(text: str) -> dict[str, str]:
    """Parse build.prop content; the last value wins, comments and imports are ignored."""
    props: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("import"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        props[key.strip()] = value.strip()
    return props

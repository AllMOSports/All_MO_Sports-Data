import json, sys, urllib.request

SCHOOLS_URL = "https://raw.githubusercontent.com/AllMOSports/All_MO_Sports-Data/refs/heads/main/output/schools.json"

def load_schools():
    with urllib.request.urlopen(SCHOOLS_URL) as r:
        return json.load(r)["schools"]

def build_lookup(schools):
    by_mshsaa, by_name = {}, {}
    for slug, s in schools.items():
        by_mshsaa.setdefault(s.get("mshsaa_name", ""), []).append(slug)
        by_name.setdefault(s.get("name", ""), []).append(slug)
    return by_mshsaa, by_name

def resolve(name, by_mshsaa, by_name):
    if name in by_mshsaa and len(by_mshsaa[name]) == 1:
        return by_mshsaa[name][0]
    if name in by_name and len(by_name[name]) == 1:
        return by_name[name][0]
    return None

def enrich_file(path, by_mshsaa, by_name):
    with open(path) as f:
        data = json.load(f)

    changed = False
    unmatched = []
    for t in data.get("teams", []):
        slug = resolve(t["school"], by_mshsaa, by_name)
        if t.get("slug") != slug:
            t["slug"] = slug
            changed = True
        if not slug:
            unmatched.append(t["school"])

    if changed:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    return changed, unmatched

if __name__ == "__main__":
    schools = load_schools()
    by_mshsaa, by_name = build_lookup(schools)

    any_changed = False
    for path in sys.argv[1:]:
        changed, unmatched = enrich_file(path, by_mshsaa, by_name)
        any_changed = any_changed or changed
        print(f"{path}: {'updated' if changed else 'no change'}")
        for n in unmatched:
            print(f"  WARNING unmatched: {n}")

    # Signal to the calling workflow whether anything changed
    sys.exit(0 if not any_changed else 78)

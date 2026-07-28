#!/usr/bin/env python3
"""
App status fetcher for the Vibe Dashboard "Apps" tab.
=====================================================
Scans locally-cloned repos for Xcode projects and, when App Store Connect API
credentials are provided, enriches each app with its live release status
(TestFlight / in review / released / etc). Writes apps_data.json.

The repo scan works with no credentials (status falls back to "Development").
Live status needs an App Store Connect API key, supplied via env vars:
    ASC_ISSUER_ID   - the issuer ID (UUID) from App Store Connect > Integrations
    ASC_KEY_ID      - the key ID of the generated API key
    ASC_PRIVATE_KEY - contents of the downloaded .p8 private key (PEM)

Usage:
    python fetch_app_status.py --path ./repos --output apps_data.json
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

# ---------------------------------------------------------------------------
# Xcode project discovery (works offline, no Apple credentials needed)
# ---------------------------------------------------------------------------

SDKROOT_PLATFORMS = {
    "iphoneos": "iOS",
    "macosx": "macOS",
    "watchos": "watchOS",
    "appletvos": "tvOS",
    "xros": "visionOS",
}

# bundle-id suffixes that indicate a helper target, not the app itself
NON_APP_HINTS = ("test", "uitest", "widget", "watchkitextension", "notificationservice",
                 "shareextension", "intents", "clip", "tests")


def _first(pattern, text):
    m = re.search(pattern, text)
    return m.group(1).strip().strip('"') if m else None


def _pick_app_bundle_id(bundle_ids):
    """Choose the app's base bundle id from all candidates in a pbxproj.

    Excludes variable references and obvious helper targets, then prefers a
    candidate that is a prefix of the others (the base app id), falling back to
    the shortest.
    """
    candidates = []
    for b in bundle_ids:
        b = b.strip().strip('"')
        if not b or "$" in b:  # skip $(inherited) / interpolated ids
            continue
        low = b.lower()
        if any(h in low for h in NON_APP_HINTS):
            continue
        candidates.append(b)
    if not candidates:
        return None
    # a bundle id that prefixes every other candidate is the base app id
    for b in sorted(candidates, key=len):
        if all(other == b or other.startswith(b + ".") for other in candidates):
            return b
    return min(candidates, key=len)


def parse_pbxproj(pbxproj_path):
    """Extract app metadata from a project.pbxproj file."""
    try:
        text = pbxproj_path.read_text(errors="ignore")
    except Exception:
        return {}

    bundle_ids = re.findall(r'PRODUCT_BUNDLE_IDENTIFIER = "?([^";\n]+)"?;', text)
    bundle_id = _pick_app_bundle_id(bundle_ids)

    version = _first(r'MARKETING_VERSION = ([^;\n]+);', text)
    build = _first(r'CURRENT_PROJECT_VERSION = ([^;\n]+);', text)

    platforms = set()
    for sdk in re.findall(r'SDKROOT = ([^;\n]+);', text):
        sdk = sdk.strip().strip('"').lower()
        for key, name in SDKROOT_PLATFORMS.items():
            if sdk.startswith(key):
                platforms.add(name)
    # Also honor explicit SUPPORTED_PLATFORMS
    for sp in re.findall(r'SUPPORTED_PLATFORMS = "?([^";\n]+)"?;', text):
        for token in sp.split():
            for key, name in SDKROOT_PLATFORMS.items():
                if token.lower().startswith(key):
                    platforms.add(name)

    is_app = "com.apple.product-type.application" in text

    return {
        "bundle_id": bundle_id,
        "version": version if version and "$" not in version else None,
        "build": build if build and "$" not in build else None,
        "platforms": sorted(platforms),
        "is_app": is_app,
    }


def discover_xcode_apps(base_path, repo_meta=None, allowed_repos=None):
    """Find Xcode-project repos under base_path and extract their app metadata.

    repo_meta: optional {repo_name: {"full_name":..., "url":...}} to enrich rows.
    allowed_repos: optional set of repo names to restrict discovery to (e.g. the
        repos already in dashboard_data.json, which are the owner's own,
        non-fork repos). When None, all cloned repos are scanned.
    """
    repo_meta = repo_meta or {}
    base = Path(base_path)
    apps = []

    for repo_dir in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
            continue
        if allowed_repos is not None and repo_dir.name not in allowed_repos:
            continue  # not one of the owner's repos (co-worker's / fork)
        # find the top-most .xcodeproj (avoid nested Pods/Carthage checkouts)
        projects = [p for p in repo_dir.rglob("*.xcodeproj")
                    if "Pods" not in p.parts and "Carthage" not in p.parts
                    and ".build" not in p.parts]
        if not projects:
            continue
        projects.sort(key=lambda p: len(p.parts))  # shallowest first
        proj = projects[0]
        meta = parse_pbxproj(proj / "project.pbxproj")

        app_name = proj.stem  # e.g. MyApp.xcodeproj -> MyApp
        rmeta = repo_meta.get(repo_dir.name, {})
        apps.append({
            "name": app_name,
            "repo": repo_dir.name,
            "full_name": rmeta.get("full_name", repo_dir.name),
            "url": rmeta.get("url", ""),
            "bundle_id": meta.get("bundle_id"),
            "version": meta.get("version"),
            "build": meta.get("build"),
            "platforms": meta.get("platforms") or [],
            "xcodeproj": str(proj.relative_to(repo_dir)),
        })
    return apps


# ---------------------------------------------------------------------------
# App Store Connect API (live release status)
# ---------------------------------------------------------------------------

# Map raw App Store version states to friendly labels + a coarse category.
STORE_STATE_LABELS = {
    "READY_FOR_SALE": ("Released", "released"),
    "IN_REVIEW": ("In Review", "review"),
    "WAITING_FOR_REVIEW": ("Waiting for Review", "review"),
    "PENDING_DEVELOPER_RELEASE": ("Pending Release", "review"),
    "PENDING_APPLE_RELEASE": ("Pending Release", "review"),
    "PROCESSING_FOR_APP_STORE": ("Processing", "review"),
    "PREPARE_FOR_SUBMISSION": ("Preparing", "development"),
    "REJECTED": ("Rejected", "rejected"),
    "DEVELOPER_REJECTED": ("Rejected", "rejected"),
    "METADATA_REJECTED": ("Metadata Rejected", "rejected"),
    "INVALID_BINARY": ("Invalid Binary", "rejected"),
    "REMOVED_FROM_SALE": ("Removed from Sale", "removed"),
    "DEVELOPER_REMOVED_FROM_SALE": ("Removed from Sale", "removed"),
    "REPLACED_WITH_NEW_VERSION": ("Superseded", "released"),
    "WAITING_FOR_EXPORT_COMPLIANCE": ("Waiting (Export Compliance)", "review"),
}

TESTFLIGHT_ACTIVE = {"READY_FOR_BETA_TESTING", "IN_BETA_TESTING", "BETA_APPROVED"}
TESTFLIGHT_REVIEW = {"WAITING_FOR_BETA_REVIEW", "IN_BETA_REVIEW"}


class ASCClient:
    BASE = "https://api.appstoreconnect.apple.com/v1"

    def __init__(self, issuer_id, key_id, private_key):
        import jwt  # PyJWT; imported lazily so the offline scan needs no crypto
        self._jwt = jwt
        self.issuer_id = issuer_id
        self.key_id = key_id
        self.private_key = private_key
        self.session = requests.Session()
        self._token = None
        self._token_exp = 0

    def _tok(self):
        now = int(time.time())
        if self._token and now < self._token_exp - 60:
            return self._token
        exp = now + 1000  # ASC allows up to 20 min; stay well under
        self._token = self._jwt.encode(
            {"iss": self.issuer_id, "iat": now, "exp": exp, "aud": "appstoreconnect-v1"},
            self.private_key, algorithm="ES256",
            headers={"kid": self.key_id, "typ": "JWT"},
        )
        self._token_exp = exp
        return self._token

    def _get(self, path, params=None):
        r = self.session.get(f"{self.BASE}{path}",
                             headers={"Authorization": f"Bearer {self._tok()}"},
                             params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def find_app(self, bundle_id):
        data = self._get("/apps", {"filter[bundleId]": bundle_id, "limit": 1}).get("data", [])
        return data[0] if data else None

    def store_state(self, app_id):
        params = {"limit": 1,
                  "fields[appStoreVersions]": "appStoreState,appVersionState,versionString"}
        data = self._get(f"/apps/{app_id}/appStoreVersions", params).get("data", [])
        if not data:
            return None
        a = data[0]["attributes"]
        return {"state": a.get("appStoreState") or a.get("appVersionState"),
                "version": a.get("versionString")}

    def testflight_state(self, app_id):
        params = {"limit": 1, "sort": "-version", "include": "buildBetaDetail",
                  "fields[buildBetaDetails]": "externalBuildState,internalBuildState"}
        resp = self._get(f"/apps/{app_id}/builds", params)
        det = next((i for i in resp.get("included", []) if i.get("type") == "buildBetaDetails"), None)
        if not det:
            return None
        a = det["attributes"]
        return {"external": a.get("externalBuildState"), "internal": a.get("internalBuildState")}


def derive_status(store, testflight, found):
    """Collapse store + testflight signals into a single headline status."""
    if store and store.get("state"):
        label, category = STORE_STATE_LABELS.get(store["state"], (store["state"].replace("_", " ").title(), "other"))
        # If not yet submitted for sale but a build is in beta, TestFlight wins.
        if category == "development" and testflight:
            ext = testflight.get("external"); intr = testflight.get("internal")
            if ext in TESTFLIGHT_ACTIVE or intr in TESTFLIGHT_ACTIVE:
                return {"label": "TestFlight", "category": "testflight"}
            if ext in TESTFLIGHT_REVIEW:
                return {"label": "TestFlight (Beta Review)", "category": "review"}
        return {"label": label, "category": category}
    if testflight:
        ext = testflight.get("external"); intr = testflight.get("internal")
        if ext in TESTFLIGHT_ACTIVE or intr in TESTFLIGHT_ACTIVE:
            return {"label": "TestFlight", "category": "testflight"}
        if ext in TESTFLIGHT_REVIEW:
            return {"label": "TestFlight (Beta Review)", "category": "review"}
        return {"label": "TestFlight (Processing)", "category": "testflight"}
    if found:
        return {"label": "Registered", "category": "development"}
    return {"label": "Development", "category": "development"}


def enrich_with_asc(apps, client):
    """Attach live App Store Connect status to each app (by bundle id)."""
    for app in apps:
        bundle_id = app.get("bundle_id")
        app["store"] = None
        app["testflight"] = None
        if not bundle_id:
            app["status"] = derive_status(None, None, False)
            app["status"]["note"] = "no bundle id parsed"
            continue
        try:
            asc_app = client.find_app(bundle_id)
            if not asc_app:
                app["status"] = derive_status(None, None, False)
                continue
            app_id = asc_app["id"]
            # Prefer ASC's marketing name over the .xcodeproj filename
            asc_name = (asc_app.get("attributes") or {}).get("name")
            if asc_name:
                app["name"] = asc_name
            store = client.store_state(app_id)
            testflight = client.testflight_state(app_id)
            app["store"] = store
            app["testflight"] = testflight
            app["status"] = derive_status(store, testflight, True)
            if store and store.get("state") in ("READY_FOR_SALE", "REPLACED_WITH_NEW_VERSION"):
                app["store"]["url"] = f"https://apps.apple.com/app/id{app_id}"
        except Exception as e:
            app["status"] = derive_status(None, None, False)
            app["status"]["note"] = f"ASC lookup failed: {e}"
    return apps


def load_repo_meta(dashboard_data_file):
    """Pull full_name/url per repo from an existing dashboard_data.json if present."""
    meta = {}
    p = Path(dashboard_data_file)
    if p.exists():
        try:
            data = json.load(open(p))
            for proj in data.get("projects", []):
                meta[proj.get("name")] = {"full_name": proj.get("full_name", ""),
                                          "url": proj.get("url", "")}
        except (json.JSONDecodeError, IOError):
            pass
    return meta


def main():
    parser = argparse.ArgumentParser(description="Fetch Xcode app inventory + App Store Connect status")
    parser.add_argument("--path", required=True, help="Directory containing cloned repos")
    parser.add_argument("--output", default="apps_data.json", help="Output JSON file")
    parser.add_argument("--dashboard-data", default="dashboard_data.json",
                        help="Existing dashboard_data.json to source repo url/full_name from")
    args = parser.parse_args()

    base = Path(args.path)
    if not base.exists():
        print(f"❌ Path not found: {base}")
        return 1

    repo_meta = load_repo_meta(args.dashboard_data)
    # Restrict to the owner's own repos (those in dashboard_data.json) so
    # co-workers' repos and forks with Xcode projects don't appear as "my apps".
    # If dashboard_data.json is absent/empty, fall back to scanning everything.
    allowed_repos = set(repo_meta) or None
    apps = discover_xcode_apps(base, repo_meta, allowed_repos=allowed_repos)
    print(f"📱 Found {len(apps)} repo(s) with an Xcode project"
          + (f" (restricted to {len(allowed_repos)} owner repos)" if allowed_repos else ""))
    for a in apps:
        print(f"   • {a['name']} ({a['repo']}) bundle={a['bundle_id']} v{a['version']}")

    issuer = os.environ.get("ASC_ISSUER_ID")
    key_id = os.environ.get("ASC_KEY_ID")
    private_key = os.environ.get("ASC_PRIVATE_KEY")
    if issuer and key_id and private_key:
        if requests is None:
            print("⚠️  'requests' not installed; skipping App Store Connect lookup")
            for a in apps:
                a["status"] = derive_status(None, None, False)
        else:
            print("🔑 Querying App Store Connect for live release status...")
            client = ASCClient(issuer, key_id, private_key)
            enrich_with_asc(apps, client)
            for a in apps:
                print(f"   • {a['name']}: {a['status']['label']}")
    else:
        print("ℹ️  No App Store Connect credentials (ASC_ISSUER_ID/ASC_KEY_ID/ASC_PRIVATE_KEY); "
              "status defaults to Development")
        for a in apps:
            a["status"] = derive_status(None, None, False)

    # Sort: by status category priority, then name
    order = {"released": 0, "review": 1, "testflight": 2, "rejected": 3,
             "removed": 4, "development": 5, "other": 6}
    apps.sort(key=lambda a: (order.get(a.get("status", {}).get("category", "other"), 9), a["name"].lower()))

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(apps),
        "apps": apps,
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n✅ Wrote {args.output} ({len(apps)} apps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

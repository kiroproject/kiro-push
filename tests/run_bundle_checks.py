"""Static checks: the JS bundles parse (node --check), the customer module's pure helper works, the build manifest entry,
the version in its three places, the locale keys and the files the manifest names.

Run:  python tests/run_bundle_checks.py     (needs node on PATH)
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT.parent


def node(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", *args], capture_output=True, text=True, encoding="utf-8")


def check_admin_bundle() -> None:
    """Admin UI: pure-logic tests, error / reason dictionaries against the backend, ESM import under a stubbed DOM, shipping."""
    done = node(str(ROOT / "tests/admin_logic_test.mjs"), str(ROOT))
    try:
        result = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        h.ok(False, f"admin logic tests ran: {done.stderr[:300]}")
        return
    for failure in result["failed"]:
        h.ok(False, f"admin logic: {failure}")
    h.ok(not result["failed"], f"admin logic tests: {result['passed']} assertions pass")

    backend = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "backend/kiro_push").glob("*.py"))
    codes = set(re.findall(r'PushError\(\s*"([a-z_0-9]+)"', backend))
    codes |= set(re.findall(r'_err\(\s*"([a-z_0-9]+)"', backend))
    codes |= set(re.findall(r'"(invalid_[a-z_0-9]+)"', backend))
    storage = (ROOT / "backend/kiro_push/storage.py").read_text(encoding="utf-8")
    config_keys = set(re.findall(r'^    "([a-z_]+)": ', storage.split("DEFAULT_CONFIG")[1].split("}")[0], re.M))
    codes |= {f"invalid_{k}" for k in config_keys}
    missing = sorted(codes - set(result["errors"]))
    h.ok(not missing, f"ERRORS covers every backend error code (missing: {missing})")
    h.ok(config_keys == set(result["configKeys"]), f"settings form covers every config key (diff: {config_keys ^ set(result['configKeys'])})")
    reasons = set(re.findall(r'_finish\(factory, d, "(?:skipped|failed)", "([a-z_]+)"', (ROOT / "backend/kiro_push/scheduler.py").read_text(encoding="utf-8")))
    reasons |= {"interrupted_during_send", "rate_limited"}  # written through helpers, not _finish literals
    gifts_src = (ROOT / "backend/kiro_push/gifts.py").read_text(encoding="utf-8")
    reasons |= set(re.findall(r'GiftOutcome\("[a-z_]+", "([a-z_]+)"', gifts_src))
    reasons |= {f"gift_{s}" for s in ("not_eligible", "unavailable", "failed")}
    reasons_missing = sorted(reasons - set(result["reasons"]))
    h.ok(not reasons_missing, f"REASONS covers scheduler / gift reasons (missing: {reasons_missing})")

    admin_uri = (ROOT / "frontend/admin/index.js").as_uri()
    script = (
        "const noop = () => {};"
        "const el = () => ({ appendChild: noop, addEventListener: noop, setAttribute: noop, style: {}, classList: { add: noop, remove: noop, toggle: noop }, querySelector: () => null, querySelectorAll: () => [], replaceChildren: noop, remove: noop, content: { firstElementChild: null } });"
        "globalThis.document = { createElement: el, body: el(), cookie: '', addEventListener: noop, removeEventListener: noop, createDocumentFragment: el, createTextNode: el };"
        "globalThis.window = { addEventListener: noop, removeEventListener: noop };"
        f"import('{admin_uri}').then(m => console.log(JSON.stringify([typeof m.mountView, typeof m.updateView, typeof m.unmountView])));"
    )
    done = node("--input-type=module", "-e", script)
    h.ok(done.returncode == 0 and json.loads(done.stdout.strip().splitlines()[-1]) == ["function", "function", "function"], f"admin bundle imports as ESM under a stubbed DOM ({done.stderr[:300]})")
    sources = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "frontend/admin").glob("*.js"))
    h.ok(".innerHTML = esc" not in sources and "document.write" not in sources and "eval(" not in sources, "admin bundle: no unsafe sinks fed raw strings")
    ru = json.loads((ROOT / "locales/ru.json").read_text(encoding="utf-8"))
    h.ok("admin_kiro_push_section_title" in ru, "locale has the section title key")
    for p in (ROOT / "frontend/admin").glob("*.js"):
        h.ok(len(p.read_text(encoding="utf-8").splitlines()) < 1500, f"{p.name} under 1500 lines")
    import_targets = set(re.findall(r'from "\./([a-z]+\.js)"', sources))
    h.ok(all((ROOT / "frontend/admin" / t).is_file() for t in import_targets), "every relative import resolves to a shipped file")


def main() -> None:
    node_path = shutil.which("node")
    h.ok(node_path is not None, "node is available")
    admin_files = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "frontend/admin").glob("*.js"))
    h.ok(len(admin_files) >= 8, f"admin bundle modules present ({len(admin_files)})")
    for rel in ("frontend/customer/index.js", *admin_files):
        done = node("--check", str(ROOT / rel))
        h.ok(done.returncode == 0, f"node --check {rel}" + ("" if done.returncode == 0 else f": {done.stderr[:300]}"))
    uri = (ROOT / "frontend/customer/index.js").as_uri()
    script = (
        f"import('{uri}').then(m => {{"
        "console.log(JSON.stringify([m.humanDate('ru', 12, 8, 1990), m.humanDate('ru', 1, 1), m.humanDate('en', 12, 8, 0),"
        " typeof m.mountView, typeof m.unmountView, typeof m.updateView]));})"
    )
    done = node("--input-type=module", "-e", script)
    h.ok(done.returncode == 0, f"customer module imports as ESM ({done.stderr[:200]})")
    values = json.loads(done.stdout)
    h.ok(values == ["12 августа, 1990", "1 января", "August 12", "function", "function", "function"], f"humanDate / exports ({values})")

    check_admin_bundle()

    spec = importlib.util.spec_from_file_location("kiro_build", PLUGINS / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    entry = build.PLUGINS["kiro-push"]
    h.ok(entry["version"] == "0.1.1" and entry["entry_point"] == "kiro_push:plugin", "build.py: version / entry point")
    h.ok(entry["requires"] == {"user_ui": 1, "ui_composition": 1, "durable_jobs": 1, "rewards": 1}, "build.py: required capabilities include user_ui")
    frontend = entry["frontend"]
    h.ok(frontend["sections"][0]["id"] == "kiro-push" and "user" in frontend and frontend["user"]["entry"] == "customer/index.js", "manifest: admin section + user entry")
    slot = frontend["user"]["slots"][0]
    h.ok(slot["target"] == "user.settings.codes" and slot["view"] == "settings-birthdate", "manifest: settings slot")
    for rel in (frontend["entry"], frontend["user"]["entry"]):
        h.ok((ROOT / "frontend" / rel).is_file(), f"manifest file exists: {rel}")
    for lang in ("ru", "en"):
        keys = json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        wanted = {frontend["sections"][0]["i18nKey"], slot["i18nKey"]}
        h.ok(wanted <= set(keys), f"locales/{lang}.json has the nav keys")
    init = (ROOT / "backend/kiro_push/__init__.py").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8") if (ROOT / "CHANGELOG.md").exists() else ""
    h.ok(re.search(r'^VERSION = "0\.1\.1"$', init, re.M) is not None, "plugin class version 0.1.1")
    h.ok("## 0.1.1 — 2026-11-12" in changelog and "## 0.1.0 — 2026-11-10" in changelog, "CHANGELOG.md has '## 0.1.1 — 2026-11-12' and '## 0.1.0'")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    api_doc = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    h.ok("0.1.1" in readme and "docs/api.md" in readme, "README has the version and links the API document")
    h.ok("## HTTP API" in api_doc and "/api/plugins/kiro-push/birthdate" in api_doc, "docs/api.md has the API contract")
    h.finish()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Download a Maven or NPM module from a public GitHub repository.

- Supports multi-module projects.
- Auto-detects Maven or NPM.
- Downloads directly from GitHub releases if possible (fast path).
- Falls back to building from source if no artifact is available in releases.
- Can handle latest release or specific tag.
"""

import requests
import zipfile
import os
import sys
import tempfile
import shutil
import subprocess
import json
from urllib.parse import urlparse
from xml.etree import ElementTree as ET


def parse_github_url(github_url):
    """Extract owner and repo name from a GitHub URL."""
    path_parts = urlparse(github_url).path.strip("/").split("/")
    if len(path_parts) < 2:
        raise ValueError("Invalid GitHub URL")
    return path_parts[0], path_parts[1].replace(".git", "")


def get_release_asset(owner, repo, version=None, module_name=None):
    """Check GitHub releases for a pre-built artifact matching the module."""
    headers = {"Accept": "application/vnd.github+json"}
    if version:
        release_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{version}"
    else:
        release_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

    r = requests.get(release_url, headers=headers)
    if r.status_code != 200:
        return None  # No release found

    release = r.json()
    assets = release.get("assets", [])
    for asset in assets:
        name = asset["name"].lower()
        if module_name and module_name.lower() not in name:
            continue
        if name.endswith((".jar", ".tgz", ".zip", ".tar.gz")):
            return asset["browser_download_url"]

    return None


def download_file(url, output_dir):
    """Download file from URL to output_dir."""
    local_filename = os.path.join(output_dir, url.split("/")[-1])
    print(f"Downloading artifact: {url}")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(local_filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return local_filename


def download_source(owner, repo, version=None):
    """Download and extract source code for the given repo."""
    headers = {"Accept": "application/vnd.github+json"}
    ref = version if version else "HEAD"
    archive_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{ref}"

    tmp_dir = tempfile.mkdtemp()
    zip_file_path = os.path.join(tmp_dir, f"{repo}.zip")

    print(f"Downloading source archive from {archive_url}")
    with requests.get(archive_url, stream=True) as r:
        r.raise_for_status()
        with open(zip_file_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
        extract_path = os.path.join(tmp_dir, "src")
        zip_ref.extractall(extract_path)

    root_folder = os.listdir(extract_path)[0]
    return os.path.join(extract_path, root_folder)


def find_module_path(root_path, module_name):
    """Find a subdirectory with the given module_name."""
    for dirpath, dirnames, _ in os.walk(root_path):
        if module_name in dirnames:
            return os.path.join(dirpath, module_name)
    raise FileNotFoundError(f"Module {module_name} not found in repo")


def detect_project_type(module_dir):
    """Detect if module is Maven or NPM."""
    if os.path.exists(os.path.join(module_dir, "pom.xml")):
        return "maven"
    if os.path.exists(os.path.join(module_dir, "package.json")):
        return "npm"
    return "unknown"


def get_version(module_dir, proj_type):
    """Get version from pom.xml or package.json."""
    if proj_type == "maven":
        pom_file = os.path.join(module_dir, "pom.xml")
        tree = ET.parse(pom_file)
        root = tree.getroot()
        ns = {'mvn': 'http://maven.apache.org/POM/4.0.0'}
        version_tag = root.find("mvn:version", ns)
        return version_tag.text if version_tag is not None else "unknown"
    elif proj_type == "npm":
        pkg_file = os.path.join(module_dir, "package.json")
        with open(pkg_file) as f:
            pkg_json = json.load(f)
        return pkg_json.get("version", "unknown")
    return "unknown"


def package_maven_module(root_path, module_path):
    """Build Maven module and return artifact path."""
    print(f"Building Maven module '{module_path}'...")
    subprocess.run(
        ["mvn", "-pl", module_path, "-am", "package", "-DskipTests"],
        cwd=root_path,
        check=True
    )

    module_target = os.path.join(root_path, module_path, "target")
    jars = [f for f in os.listdir(module_target) if f.endswith(".jar")]
    if not jars:
        raise FileNotFoundError(f"No JAR produced for module {module_path}")

    jar_path = os.path.join(module_target, jars[0])
    output_path = os.path.join(os.getcwd(), jars[0])
    shutil.copyfile(jar_path, output_path)
    print(f"Packaged: {output_path}")
    return output_path


def package_npm_module(module_dir):
    """Build NPM module and return artifact path."""
    print(f"Packing NPM module at {module_dir}...")
    subprocess.run(["npm", "install"], cwd=module_dir, check=True)
    subprocess.run(["npm", "pack"], cwd=module_dir, check=True)
    tgzs = [f for f in os.listdir(module_dir) if f.endswith(".tgz")]
    if not tgzs:
        raise FileNotFoundError("No .tgz produced in npm pack")
    tgz_path = os.path.join(module_dir, tgzs[0])
    output_path = os.path.join(os.getcwd(), tgzs[0])
    shutil.copyfile(tgz_path, output_path)
    print(f"Packaged: {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python download_and_package.py <GitHub Repo URL> <module_name(s) comma-separated> [version/tag]")
        sys.exit(1)

    github_url = sys.argv[1]
    module_names = sys.argv[2].split(",")
    version = sys.argv[3] if len(sys.argv) >= 4 else None

    owner, repo = parse_github_url(github_url)

    for module_name in module_names:
        print(f"\n=== Processing module: {module_name} ===")

        # 1️⃣ Fast path — try releases first
        asset_url = get_release_asset(owner, repo, version, module_name)
        if asset_url:
            print("Found artifact in GitHub release — downloading directly.")
            download_file(asset_url, os.getcwd())
            continue

        # 2️⃣ Slow path — build from source
        root_path = download_source(owner, repo, version)
        module_path = find_module_path(root_path, module_name)
        proj_type = detect_project_type(module_path)
        detected_version = version or get_version(module_path, proj_type)
        print(f"Detected version: {detected_version}")

        if proj_type == "maven":
            package_maven_module(root_path, os.path.relpath(module_path, root_path))
        elif proj_type == "npm":
            package_npm_module(module_path)
        else:
            print(f"Unknown project type for {module_path}.")

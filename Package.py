import os
import re
import sys
import tempfile
import zipfile
import tarfile
import subprocess
import requests
from urllib.parse import urlparse

GITHUB_API = "https://api.github.com"

def parse_github_url(repo_url):
    """
    Returns (owner, repo, branch, module_path)
    """
    path_parts = urlparse(repo_url).path.strip("/").split("/")
    owner, repo = path_parts[0], path_parts[1]
    branch, module_path = None, None
    if len(path_parts) > 4 and path_parts[2] == "tree":
        branch = path_parts[3]
        module_path = "/".join(path_parts[4:])
    return owner, repo, branch, module_path

def get_latest_release(owner, repo):
    r = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest")
    if r.status_code == 200:
        data = r.json()
        return data["tag_name"], data.get("assets", [])
    return None, []

def fast_path_download(assets, module_name):
    for asset in assets:
        if module_name in asset["name"]:
            url = asset["browser_download_url"]
            print(f"Downloading release asset: {url}")
            r = requests.get(url)
            with open(asset["name"], "wb") as f:
                f.write(r.content)
            print(f"Downloaded {asset['name']}")
            return True
    return False

def download_and_build(owner, repo, branch, module_path):
    archive_url = f"https://github.com/{owner}/{repo}/archive/{branch or 'HEAD'}.zip"
    print(f"Downloading source archive: {archive_url}")
    r = requests.get(archive_url)
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "src.zip")
        with open(zip_path, "wb") as f:
            f.write(r.content)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)
        extracted_dir = os.path.join(tmpdir, os.listdir(tmpdir)[0])
        if module_path:
            build_dir = os.path.join(extracted_dir, module_path)
        else:
            build_dir = extracted_dir
        
        # Detect build tool
        if os.path.exists(os.path.join(build_dir, "pom.xml")):
            print("Detected Maven project")
            subprocess.run(["mvn", "-f", build_dir, "package", "-DskipTests"], check=True)
            target_dir = os.path.join(build_dir, "target")
            for file in os.listdir(target_dir):
                if file.endswith(".jar"):
                    dest = os.path.join(os.getcwd(), file)
                    os.rename(os.path.join(target_dir, file), dest)
                    print(f"Copied {file} to {dest}")
        elif os.path.exists(os.path.join(build_dir, "package.json")):
            print("Detected NPM project")
            subprocess.run(["npm", "install"], cwd=build_dir, check=True)
            subprocess.run(["npm", "pack"], cwd=build_dir, check=True)
            for file in os.listdir(build_dir):
                if file.endswith(".tgz"):
                    dest = os.path.join(os.getcwd(), file)
                    os.rename(os.path.join(build_dir, file), dest)
                    print(f"Copied {file} to {dest}")
        else:
            print("Unknown project type")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <github_repo_url>")
        sys.exit(1)

    repo_url = sys.argv[1]
    owner, repo, branch, module_path = parse_github_url(repo_url)
    module_name = module_path.split("/")[-1] if module_path else repo

    tag, assets = get_latest_release(owner, repo)
    if assets and fast_path_download(assets, module_name):
        sys.exit(0)
    
    print("No release asset found. Falling back to source build.")
    download_and_build(owner, repo, branch, module_path)

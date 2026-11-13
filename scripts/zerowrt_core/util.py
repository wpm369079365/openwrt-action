"""Shared helpers for the ZeroWrt build tooling.

Only the Python standard library is used here so the CLI can run on a bare
GitHub Actions runner before any build dependency has been installed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from contextlib import ExitStack
from pathlib import Path


class BuildError(RuntimeError):
    """Raised when a build step cannot continue."""


def log(message: str) -> None:
    print(f"[zerowrt] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[zerowrt] WARNING: {message}", flush=True)


def header(message: str) -> None:
    print(f"\n[zerowrt] ===== {message} =====", flush=True)


def which(name: str) -> str | None:
    return shutil.which(name)


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read JSON from {path}: {exc}") from exc


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def replace_text(path: Path, old: str, new: str, *, required: bool = False) -> int:
    """Replace literal text in *path* and return the number of replacements."""
    text = read_text(path)
    count = text.count(old)
    if count:
        write_text(path, text.replace(old, new))
    elif required:
        raise BuildError(f"{path}: expected text not found: {old!r}")
    else:
        warn(f"{path}: text not found (skipped): {old!r}")
    return count


def sub(path: Path, pattern: str, repl: str, *, flags: int = 0, required: bool = False) -> int:
    """Regex substitution helper returning the number of matches."""
    text = read_text(path)
    new_text, count = re.subn(pattern, repl, text, flags=flags)
    if count:
        write_text(path, new_text)
    elif required:
        raise BuildError(f"{path}: pattern not found: {pattern!r}")
    else:
        warn(f"{path}: pattern not found (skipped): {pattern!r}")
    return count


def remove_path(path: Path) -> bool:
    """Remove a file, symlink or directory tree. Returns True when removed."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return True
        if path.is_dir():
            shutil.rmtree(path)
            return True
    except FileNotFoundError:
        return False
    return False


def run(
    cmd,
    *,
    cwd=None,
    env=None,
    check=True,
    capture=False,
    quiet=False,
    log_path=None,
    display=None,
    timeout=None,
    input_text: str | None = None,
):
    """Run a command.

    ``display`` replaces the command in the log, which is used for commands
    containing values that must not be printed (tokens, passwords, ...).
    ``input_text`` is fed to stdin and is never echoed.
    """
    cmd = [str(part) for part in cmd]
    shown = display or " ".join(shlex.quote(part) for part in cmd)
    if not quiet:
        log(f"$ {shown}")

    if capture or input_text is not None:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        output = proc.stdout or ""
        if not quiet and output.strip():
            print(output.rstrip(), flush=True)
        if check and proc.returncode != 0:
            raise BuildError(f"command failed ({proc.returncode}): {shown}\n{output[-4000:]}")
        return proc.returncode, output

    with ExitStack() as stack:
        log_file = None
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            log_file = stack.enter_context(open(log_path, "a", encoding="utf-8", errors="replace"))

        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if not quiet:
                sys.stdout.write(line)
                sys.stdout.flush()
            if log_file is not None:
                log_file.write(line)
        code = proc.wait(timeout=timeout)
        proc.stdout.close()

    if check and code != 0:
        raise BuildError(f"command failed ({code}): {shown}")
    return code, ""


def capture(cmd, **kwargs) -> str:
    kwargs.setdefault("quiet", True)
    code, output = run(cmd, capture=True, **kwargs)
    if code != 0:
        raise BuildError(f"command failed: {' '.join(str(part) for part in cmd)}")
    return output.strip()


def sudo_run(cmd, **kwargs):
    if is_root():
        return run(cmd, **kwargs)
    return run(["sudo", *[str(part) for part in cmd]], **kwargs)


def sudo_output(cmd) -> str:
    code, output = sudo_run(cmd, capture=True, quiet=True)
    if code != 0:
        raise BuildError(f"command failed: {' '.join(str(part) for part in cmd)}")
    return output.strip()


def download(url: str, dest: Path, *, retries: int = 3, timeout: int = 120) -> Path:
    """Download *url* to *dest* with a few retries and a progress hint."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            log(f"downloading {url} (attempt {attempt}/{retries})")
            request = urllib.request.Request(url, headers={"User-Agent": "ZeroWrt-Builder/2.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                total = int(response.headers.get("Content-Length") or 0)
                written = 0
                with open(dest, "wb") as handle:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if total and written % (10 * 1024 * 1024) < 1024 * 256:
                            log(f"  {written // (1024 * 1024)} MiB / {total // (1024 * 1024)} MiB")
            return dest
        except Exception as exc:  # noqa: BLE001 - network errors are varied
            last_error = exc
            warn(f"download failed: {exc}")
            time.sleep(2 * attempt)
    raise BuildError(f"unable to download {url}: {last_error}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_environment(token: str | None) -> dict:
    """Environment for an authenticated git command.

    The token is passed through git's command-scope configuration instead of
    the command line or the repository's ``.git/config`` so that it cannot be
    persisted or accidentally printed.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    return env


def clone(
    repo: str,
    dest: Path,
    *,
    ref: str | None = None,
    token: str | None = None,
    depth: int = 1,
) -> str:
    """Clone ``owner/name`` into *dest* and return the checked out commit.

    The repository name is never echoed in logs together with a token.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
        raise BuildError("invalid repository name; expected 'owner/name'")

    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise BuildError(f"destination already exists and is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    url = f"https://github.com/{repo}.git"
    env = git_environment(token)
    target = ref or "HEAD"
    display = "git clone <repository> (authenticated)" if token else f"git clone {repo}"

    run(["git", "init", "-q", str(dest)], quiet=True, display=f"git init {dest.name}")
    run(
        ["git", "-C", str(dest), "remote", "add", "origin", url],
        env=env,
        quiet=True,
        display="git remote add <origin>",
    )
    run(
        ["git", "-C", str(dest), "fetch", "--depth", str(depth), "--no-tags", "origin", target],
        env=env,
        display=f"{display} @ {target}",
    )
    run(
        ["git", "-C", str(dest), "checkout", "-q", "--detach", "FETCH_HEAD"],
        env=env,
        display="git checkout FETCH_HEAD",
    )
    _, sha = run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture=True, quiet=True)
    return sha.strip()


def git_head(path: Path) -> str:
    code, output = run(["git", "-C", str(path), "rev-parse", "HEAD"], capture=True, quiet=True, check=False)
    return output.strip() if code == 0 else ""


def is_git_repo(path: Path) -> bool:
    return (Path(path) / ".git").exists()

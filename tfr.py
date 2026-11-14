"""The Farmer Was Replaced 存档层控制核心。

游戏把一切放在 LocalLow 下的纯文本里：
  Saves/SaveN/main.py, f0.py ...      玩家代码
  Saves/SaveN/__builtins__.py         内置 API 文档（签名 + docstring）
  Saves/SaveN/save.json               资源、解锁、打开的文档
  output.txt                          最近一次运行的输出/报错
  options.txt                         含 "file watcher" 开关

本模块只做四件事：读文档、读写代码、发键执行、按输出做验收。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

GAME_EXE = "TheFarmerWasReplaced.exe"
# 没有 libraryfolders.vdf 时的兜底库根目录；有 vdf 就以 vdf 为准。
STEAM_ROOTS = (
    r"E:\Program Files (x86)\Steam",
    r"C:\Program Files (x86)\Steam",
    r"D:\SteamLibrary",
)
VDF_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')
HEAD_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<kind>def|class)\s+(?P<name>\w+)")
CODE_NAME_RE = re.compile(r"^[A-Za-z_]\w*$")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def default_user_dir() -> Path:
    base = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(base) / "AppData" / "LocalLow" / "TheFarmerWasReplaced" / "TheFarmerWasReplaced"


def resolve_user_dir(cfg: dict | None = None) -> Path:
    raw = str((cfg or {}).get("user_dir") or "").strip()
    return Path(raw) if raw else default_user_dir()


def resolve_game_dir(cfg: dict | None = None) -> Path | None:
    raw = str((cfg or {}).get("game_dir") or "").strip()
    if raw:
        return Path(raw)
    for lib in steam_libraries():
        path = lib / "steamapps" / "common" / "The Farmer Was Replaced"
        if (path / GAME_EXE).exists():
            return path
    return None


def vdf_library_paths(text: str) -> list[Path]:
    """libraryfolders.vdf 里每个库的 "path"（反斜杠是转义过的）。"""
    return [Path(raw.replace("\\\\", "\\")) for raw in VDF_PATH_RE.findall(text or "")]


def steam_libraries() -> list[Path]:
    """注册表 SteamPath + 兜底根目录，再展开各自 libraryfolders.vdf 里的库。"""
    roots = [Path(r) for r in STEAM_ROOTS]
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            roots.insert(0, Path(winreg.QueryValueEx(key, "SteamPath")[0]))
    except (ImportError, OSError):
        pass
    libs: list[Path] = []
    for root in roots:
        try:
            text = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for lib in [root, *vdf_library_paths(text)]:
            if lib not in libs:
                libs.append(lib)
    return libs


def verify_enabled(cfg: dict | None, game: Path | None) -> bool:
    """verify_game：auto = 检测到游戏安装才开；on / off 强制开关。"""
    mode = str((cfg or {}).get("verify_game", "auto")).strip().lower()
    if mode == "auto":
        exe = str((cfg or {}).get("game_exe") or GAME_EXE)
        return bool(game and (Path(game) / exe).exists())
    return mode in ("on", "true")


def save_dir(user: Path, save_name: str = "Save0") -> Path:
    return Path(user) / "Saves" / (save_name or "Save0")


# --------------------------------------------------------------------- docs


def parse_builtins(text: str) -> list[dict]:
    """从 __builtins__.py 提取 name / signature / docstring。"""
    lines = text.splitlines()
    out: list[dict] = []
    i = 0
    while i < len(lines):
        head = HEAD_RE.match(lines[i])
        if not head:
            i += 1
            continue

        sig = [lines[i].strip()]
        j = i
        while j < len(lines) and not re.sub(r"#.*$", "", lines[j]).rstrip().endswith(":"):
            j += 1
            if j >= len(lines) or j - i > 8:
                break
            sig.append(lines[j].strip())

        doc = ""
        k = j + 1
        while k < len(lines) and k - j <= 8:
            stripped = lines[k].strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                quote = stripped[:3]
                body = [stripped[3:]]
                k += 1
                while k < len(lines):
                    line = lines[k]
                    if quote in line:
                        body.append(line.split(quote)[0])
                        break
                    body.append(line)
                    k += 1
                doc = "\n".join(body).strip()
                break
            if stripped and not stripped.startswith("#"):
                break
            k += 1

        out.append(
            {
                "kind": head.group("kind"),
                "name": head.group("name"),
                "signature": " ".join(sig),
                "doc": doc,
            }
        )
        i = max(j, i) + 1
    return out


def docs_path(user: Path, save_name: str) -> Path:
    return save_dir(user, save_name) / "__builtins__.py"


def load_docs(user: Path, save_name: str) -> list[dict]:
    path = docs_path(user, save_name)
    if not path.exists():
        raise FileNotFoundError(f"找不到 API 文档：{path}")
    return parse_builtins(path.read_text(encoding="utf-8", errors="replace"))


def search_docs(entries: list[dict], query: str, limit: int = 20) -> list[dict]:
    needle = (query or "").strip().lower()
    if not needle:
        names = [e["name"] for e in entries if e["kind"] == "def" and not e["name"].startswith("_")]
        return [{"names": sorted(set(names)), "count": len(set(names))}]
    hits = []
    for entry in entries:
        haystack = f"{entry['signature']}\n{entry['doc']}".lower()
        if needle in entry["name"].lower() or needle in haystack:
            hits.append(
                {
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "signature": entry["signature"],
                    "doc": entry["doc"][:600],
                }
            )
    return hits[: max(1, min(limit, 50))]


# --------------------------------------------------------------------- code


def list_code_files(sdir: Path) -> list[str]:
    if not sdir.exists():
        return []
    return sorted(
        p.stem for p in sdir.glob("*.py") if p.name != "__builtins__.py" and p.is_file()
    )


def code_mtimes(sdir: Path) -> dict:
    return {
        p.name: p.stat().st_mtime
        for p in Path(sdir).glob("*.py")
        if p.name != "__builtins__.py" and p.is_file()
    }


def read_code(sdir: Path, name: str) -> str:
    return code_path(sdir, name).read_text(encoding="utf-8", errors="replace")


def code_path(sdir: Path, name: str) -> Path:
    raw = (name or "").strip()
    if raw.endswith(".py"):
        raw = raw[:-3]
    if not CODE_NAME_RE.match(raw):
        raise ValueError(f"非法文件名：{name!r}（只允许字母数字下划线）")
    path = Path(sdir) / f"{raw}.py"
    if not path.exists() and not (raw == "main" or re.fullmatch(r"f\d+", raw)):
        raise ValueError(f"文件不存在：{path.name}（可新建 main 或 f<数字>）")
    return path


def write_code(sdir: Path, name: str, code: str, verify: bool = False) -> dict:
    path = code_path(sdir, name)
    if len(code.encode("utf-8")) > 200_000:
        raise ValueError("代码超过 200KB，拒绝写入")
    backup = None
    if path.exists():
        bdir = Path(sdir) / ".bak"
        bdir.mkdir(exist_ok=True)
        backup = bdir / f"{path.name}.{now_stamp()}.bak"
        shutil.copy2(path, backup)
    text = (code or "").replace("\r\n", "\n").replace("\r", "\n")
    tmp = path.with_name(f".{path.name}.tmp-{time.time_ns()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    out = {"file": path.name, "bytes": len(text.encode("utf-8")), "backup": str(backup) if backup else None}
    if verify:
        out["verified"] = path.read_text(encoding="utf-8") == text
    return out


# ------------------------------------------------------------------ options


def options_path(user: Path) -> Path:
    return Path(user) / "options.txt"


def read_options(user: Path) -> dict:
    path = options_path(user)
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            out[key.strip()] = value.strip()
    return out


def set_option(user: Path, key: str, value: str) -> dict:
    allowed = {"file watcher", "autosave", "autosave progress", "print warnings"}
    if key not in allowed:
        raise ValueError(f"只允许修改：{sorted(allowed)}")
    path = options_path(user)
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.split(" = ", 1)[0].strip() == key:
            lines[i] = f"{key} = {value}"
            found = True
            break
    if not found:
        lines.append(f"{key} = {value}")
    backup = path.with_name(f"{path.name}.bak-{now_stamp()}")
    shutil.copy2(path, backup)
    tmp = path.with_name(f".options.txt.tmp-{time.time_ns()}")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {"option": key, "value": value, "backup": str(backup)}


# -------------------------------------------------------------------- state


def read_save(sdir: Path) -> dict:
    return json.loads((Path(sdir) / "save.json").read_text(encoding="utf-8"))


def items(save: dict) -> dict:
    rows = ((save.get("items") or {}).get("serializeList") or [])
    return {str(r.get("name")): float(r.get("nr", 0)) for r in rows if r.get("name")}


def save_summary(save: dict) -> dict:
    return {
        "version": save.get("version"),
        "items": items(save),
        "unlocks": len(save.get("unlocks") or []),
        "active_docs": [d.get("value") for d in (save.get("openDocPages") or [])],
    }


def output_path(user: Path) -> Path:
    return Path(user) / "output.txt"


def read_output(user: Path) -> str:
    path = output_path(user)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def output_state(user: Path) -> tuple[int, int]:
    """output.txt 的 (mtime_ns, 字节数)。用来判断这次按键后游戏到底有没有写新输出。"""
    try:
        st = output_path(user).stat()
    except OSError:
        return 0, 0
    return st.st_mtime_ns, st.st_size


def parse_output(text: str) -> dict:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    return {
        "errors": [line for line in lines if line.startswith("Error:")],
        "warnings": [line for line in lines if line.startswith("Warning:")],
        "text": text,
    }


# ------------------------------------------------------------------ process


def game_pid(exe: str = GAME_EXE) -> int | None:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for row in csv.reader(io.StringIO(out)):
        if row and row[0].lower() == exe.lower():
            try:
                return int(row[1])
            except (IndexError, ValueError):
                return None
    return None


def is_game_running(exe: str = GAME_EXE) -> bool:
    return game_pid(exe) is not None


def find_window(pid: int):
    if os.name != "nt":
        return None
    import ctypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        proc = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value == pid and user32.IsWindowVisible(hwnd):
            found.append((hwnd, user32.GetWindowTextLengthW(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    titled = [hwnd for hwnd, length in found if length > 0]
    return (titled or [hwnd for hwnd, _ in found] or [None])[0]


def send_game_keys(exe: str = GAME_EXE, stop_first: bool = True, press_run: bool = True) -> dict:
    """把游戏窗口切到前台再发键。stop_first 指定先按 Shift+F5 停，press_run 指定再按 F5 开。

    只认游戏进程的窗口。只想停不想重新开，调 stop_game_keys()。
    """
    if os.name != "nt":
        raise RuntimeError("只支持 Windows")
    pid = game_pid(exe)
    if pid is None:
        raise RuntimeError(f"{exe} 没在运行")
    hwnd = find_window(pid)
    if not hwnd:
        raise RuntimeError("找不到游戏窗口")

    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    foreground = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    cur_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != cur_thread:
        attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 0x0002, 0)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    if attached:
        user32.AttachThreadInput(cur_thread, fg_thread, False)
    time.sleep(0.25)
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError("无法把游戏窗口切到前台，已取消发键（不会把按键发给别的窗口）")

    vk_shift, vk_f5, keyup = 0x10, 0x74, 0x0002

    def tap(vk):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, keyup, 0)

    steps = []
    if stop_first:
        user32.keybd_event(vk_shift, 0, 0, 0)
        tap(vk_f5)
        user32.keybd_event(vk_shift, 0, keyup, 0)
        steps.append("Shift+F5")
        time.sleep(0.4)
    if press_run:
        tap(vk_f5)
        steps.append("F5")
    return {"pid": pid, "hwnd": int(hwnd), "sent": " then ".join(steps)}


def stop_game_keys(exe: str = GAME_EXE) -> dict:
    """只按 Shift+F5 停住执行，不再按 F5 重启。"""
    return send_game_keys(exe=exe, stop_first=True, press_run=False)


def run_and_check(user: Path, sdir: Path, wait_seconds: float = 6.0, exe: str = GAME_EXE) -> dict:
    before_text = read_output(user)
    before_state = output_state(user)
    before_items = items(read_save(sdir))
    sent = send_game_keys(exe=exe)
    limit = max(1.0, min(float(wait_seconds or 6), 60.0))
    started = time.monotonic()
    deadline = started + limit
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if output_state(user) != before_state:
            break
    waited = round(min(limit, time.monotonic() - started), 2)
    after_text = read_output(user)
    after_state = output_state(user)
    after_items = items(read_save(sdir))

    delta_text = after_text[len(before_text):] if after_text.startswith(before_text) else after_text
    parsed = parse_output(delta_text)
    delta_items = {
        key: round(after_items.get(key, 0.0) - before_items.get(key, 0.0), 3)
        for key in set(before_items) | set(after_items)
        if after_items.get(key, 0.0) != before_items.get(key, 0.0)
    }
    if parsed["errors"]:
        verdict = "failed"
    elif parsed["warnings"]:
        verdict = "warning"
    elif after_text != before_text or delta_items:
        verdict = "ok"
    else:
        verdict = "no_change"
    return {
        "verdict": verdict,
        "sent": sent,
        "output_changed": after_state != before_state,
        "output_state_before": list(before_state),
        "output_state_after": list(after_state),
        "errors": parsed["errors"],
        "warnings": parsed["warnings"],
        "new_output": delta_text[:8000],
        "item_delta": delta_items,
        "waited_seconds": waited,
    }


# ---------------------------------------------------------------- self-check


def _selftest() -> None:
    import tempfile

    sample = (
        "def harvest() -> _bool:\n"
        '\t"""\n\tHarvests the current crop.\n\n\ttakes `1` tick.\n\t"""\n\t...\n\n'
        "class Items(_Enum):\n"
        '\t"""All items."""\n'
    )
    parsed = parse_builtins(sample)
    assert [p["name"] for p in parsed] == ["harvest", "Items"]
    assert parsed[0]["signature"].startswith("def harvest()")
    assert "Harvests" in parsed[0]["doc"]
    assert search_docs(parsed, "tick")[0]["name"] == "harvest"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sdir = save_dir(root, "Save0")
        sdir.mkdir(parents=True)
        (sdir / "main.py").write_text("print('hi')\n", encoding="utf-8")
        (sdir / "save.json").write_text(
            json.dumps({"version": 3, "unlocks": ["a", "b"], "items": {"serializeList": [{"name": "hay", "nr": 5}]}}),
            encoding="utf-8",
        )
        (root / "options.txt").write_text("file watcher = disabled\n", encoding="utf-8")
        (root / "output.txt").write_text("Warning: x\n", encoding="utf-8")

        assert list_code_files(sdir) == ["main"]
        written = write_code(sdir, "main", "print('bye')\r\n", verify=True)
        assert written["backup"] and written["verified"] and read_code(sdir, "main") == "print('bye')\n"
        assert set_option(root, "file watcher", "enabled")["value"] == "enabled"
        assert read_options(root)["file watcher"] == "enabled"
        assert items(read_save(sdir)) == {"hay": 5.0}
        assert parse_output(read_output(root))["warnings"] == ["Warning: x"]
        vdf = '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"E:\\\\Steam"\n\t}\n}\n'
        assert vdf_library_paths(vdf) == [Path(r"E:\Steam")]
        assert not verify_enabled({"verify_game": "off"}, root) and verify_enabled({"verify_game": "on"}, None)
        (root / "custom.exe").write_bytes(b"")
        assert verify_enabled({"game_exe": "custom.exe"}, root)
        with tempfile.TemporaryDirectory() as gate:
            base = Path(gate)
            save = base / "Saves" / "Save0"
            save.mkdir(parents=True)
            written = write_code(save, "main", "print(1)\n")
            assert written["bytes"] > 0 and written["backup"] is None
            assert not [p for p in save.iterdir() if ".tmp-" in p.name]
            assert output_state(base) == (0, 0)
            (base / "output.txt").write_text("x\n", encoding="utf-8")
            assert output_state(base) != (0, 0)
            assert callable(stop_game_keys)
        print("selftest ok")


if __name__ == "__main__":
    _selftest()

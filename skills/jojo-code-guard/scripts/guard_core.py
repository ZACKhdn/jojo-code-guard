#!/usr/bin/env python3
"""啾啾代码守护的跨平台字节级检查核心。"""

from __future__ import annotations

import dataclasses
import difflib
import os
import pathlib
import platform
import shutil
import subprocess
from typing import Iterable, Optional, Sequence


UTF8_BOM = b"\xef\xbb\xbf"
UTF16_LE_BOM = b"\xff\xfe"
UTF16_BE_BOM = b"\xfe\xff"
UTF32_LE_BOM = b"\xff\xfe\x00\x00"
UTF32_BE_BOM = b"\x00\x00\xfe\xff"

TEXT_SUFFIXES = {
    ".bat", ".c", ".cc", ".cfg", ".cmake", ".cmd", ".cpp", ".cs", ".css",
    ".cxx", ".h", ".hh", ".hlsl", ".hpp", ".htm", ".html", ".hxx", ".ini",
    ".inl", ".ipp", ".ixx", ".java", ".js", ".json", ".jsonc", ".md", ".m", ".mm",
    ".frag", ".glsl", ".props", ".proto", ".ps1", ".py", ".rc", ".rc2", ".sln", ".sh",
    ".sql", ".targets", ".toml", ".ts", ".txt", ".vcxproj", ".vert", ".xml", ".yaml", ".yml",
}
TEXT_NAMES = {
    ".editorconfig", ".gitattributes", ".gitignore", ".gitmodules", "AGENTS.md", "CMakeLists.txt",
    "Dockerfile", "Makefile", "session-start",
}
TOOL_TEXT_SUFFIXES = {
    ".css", ".frag", ".glsl", ".hlsl", ".html", ".props", ".proto", ".sln", ".svg", ".targets", ".vcxproj", ".vert", ".xml",
}


@dataclasses.dataclass(frozen=True)
class TextInfo:
    """描述文件的可验证字节属性。"""

    encoding: str
    bom: str
    eol: str
    final_newline: bool
    text: Optional[str]
    binary: bool
    error: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class Diagnostic:
    """描述一条检查结果。"""

    level: str
    code: str
    path: str
    message: str


def run_git(repo: pathlib.Path, arguments: Sequence[str], check: bool = True) -> bytes:
    """执行 Git 并保留原始输出字节。"""
    result = subprocess.run(
        ["git", *arguments],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("Git 命令失败：git %s\n%s" % (" ".join(arguments), message))
    return result.stdout


def check_conversion_policy(repo: pathlib.Path, staged: bool) -> list[Diagnostic]:
    """在 Git 可能改写工作区换行时提示，避免丢失老文件基线。"""
    diff_arguments = ["diff"]
    if staged:
        diff_arguments.insert(1, "--cached")
    diff_result = subprocess.run(
        ["git", *diff_arguments, "--quiet"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if diff_result.returncode == 0:
        return []
    changed_paths = _decode_paths(run_git(repo, diff_arguments + ["--name-only", "-z"], check=False))
    if not any(is_text_path(path) for path in changed_paths):
        return []

    config_values: list[tuple[str, str]] = []
    for key in ("core.autocrlf", "core.eol"):
        result = subprocess.run(
            ["git", "config", "--get", key],
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        value = result.stdout.decode("utf-8", errors="replace").strip()
        if value:
            config_values.append((key, value))
    risky = [
        (key, value)
        for key, value in config_values
        if (key == "core.autocrlf" and value.lower() not in {"false", "0"})
        or (key == "core.eol" and value.lower() not in {"native", "unset"})
    ]
    if not risky:
        return []
    details = ", ".join(f"{key}={value}" for key, value in risky)
    level = "BLOCKED" if staged else "WARNING"
    return [
        Diagnostic(
            level,
            "GIT_CONVERSION_POLICY",
            "Git",
            f"检测到 {details}；Git 可能已改写工作区换行，无法可靠恢复老文件基线。请先设置 "
            "git config --local core.autocrlf false，并确认 .gitattributes，再检查 diff",
        )
    ]


def find_repo(start: pathlib.Path | str = ".") -> pathlib.Path:
    """定位当前工作树根目录，并兼容 Git worktree。"""
    start_path = pathlib.Path(start).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise RuntimeError(f"无法访问工作目录：{start_path}（{error}）") from error
    if result.returncode != 0:
        raise RuntimeError("当前目录不是 Git 工作树")
    return pathlib.Path(os.fsdecode(result.stdout.strip())).resolve()


def inspect_bytes(data: bytes) -> TextInfo:
    """严格识别常见编码、BOM 和换行，不使用有损文件解码。"""
    encoding = "utf-8"
    bom = "none"
    payload = data
    try:
        if data.startswith(UTF32_LE_BOM):
            encoding, bom, payload = "utf-32le", "utf-32le", data[4:]
            text = payload.decode("utf-32-le", errors="strict")
        elif data.startswith(UTF32_BE_BOM):
            encoding, bom, payload = "utf-32be", "utf-32be", data[4:]
            text = payload.decode("utf-32-be", errors="strict")
        elif data.startswith(UTF8_BOM):
            encoding, bom, payload = "utf-8", "utf-8", data[3:]
            text = payload.decode("utf-8", errors="strict")
        elif data.startswith(UTF16_LE_BOM):
            encoding, bom, payload = "utf-16le", "utf-16le", data[2:]
            text = payload.decode("utf-16-le", errors="strict")
        elif data.startswith(UTF16_BE_BOM):
            encoding, bom, payload = "utf-16be", "utf-16be", data[2:]
            text = payload.decode("utf-16-be", errors="strict")
        else:
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                try:
                    encoding = "cp936"
                    text = data.decode("cp936", errors="strict")
                except UnicodeDecodeError:
                    encoding = "gb18030"
                    text = data.decode("gb18030", errors="strict")
    except (UnicodeDecodeError, LookupError) as error:
        binary = b"\x00" in data
        return TextInfo(
            "binary" if binary else "unknown",
            bom,
            "binary" if binary else "unknown",
            False,
            None,
            binary,
            str(error),
        )

    if b"\x00" in data and encoding not in {"utf-16le", "utf-16be", "utf-32le", "utf-32be"}:
        return TextInfo("binary", bom, "binary", False, None, True, "包含 NUL 字节")

    crlf_count = text.count("\r\n")
    remaining = text.replace("\r\n", "")
    lf_count = remaining.count("\n")
    cr_count = remaining.count("\r")
    kinds = sum(bool(value) for value in (crlf_count, lf_count, cr_count))
    if kinds == 0:
        eol = "none"
    elif kinds > 1:
        eol = "mixed"
    elif crlf_count:
        eol = "crlf"
    elif lf_count:
        eol = "lf"
    else:
        eol = "cr"
    return TextInfo(encoding, bom, eol, text.endswith(("\n", "\r")), text, False)


def is_text_path(path: str) -> bool:
    """根据文件名筛选需要保护的常见文本文件。"""
    item = pathlib.PurePosixPath(path.replace("\\", "/"))
    return item.name in TEXT_NAMES or item.suffix.lower() in TEXT_SUFFIXES


def _line_parts(text: str) -> list[tuple[str, str]]:
    """保留每一行的原始换行符。"""
    parts: list[tuple[str, str]] = []
    start = 0
    index = 0
    while index < len(text):
        if text[index] == "\r":
            ending = "\r\n" if index + 1 < len(text) and text[index + 1] == "\n" else "\r"
            parts.append((text[start:index], ending))
            index += len(ending)
            start = index
        elif text[index] == "\n":
            parts.append((text[start:index], "\n"))
            index += 1
            start = index
        else:
            index += 1
    if start < len(text) or not parts:
        parts.append((text[start:], ""))
    return parts


def compare_existing(path: str, old_data: bytes, new_data: bytes) -> list[Diagnostic]:
    """检查已有文件是否发生了隐式编码、BOM 或换行迁移。"""
    old = inspect_bytes(old_data)
    new = inspect_bytes(new_data)
    diagnostics: list[Diagnostic] = []
    if old.binary or new.binary:
        if old.binary != new.binary:
            diagnostics.append(Diagnostic("BLOCKED", "BINARY_TEXT_CHANGED", path, "二进制/文本类型发生变化"))
        return diagnostics
    if old.encoding != new.encoding:
        diagnostics.append(
            Diagnostic("BLOCKED", "ENCODING_CHANGED", path, f"编码发生变化：{old.encoding} -> {new.encoding}")
        )
    if old.bom != new.bom:
        diagnostics.append(Diagnostic("BLOCKED", "BOM_CHANGED", path, f"BOM 发生变化：{old.bom} -> {new.bom}"))
    if old.error or new.error or old.text is None or new.text is None:
        diagnostics.append(
            Diagnostic("BLOCKED", "UNKNOWN_ENCODING", path, new.error or old.error or "无法严格识别编码")
        )
        return diagnostics

    old_normal = old.text.replace("\r\n", "\n").replace("\r", "\n")
    new_normal = new.text.replace("\r\n", "\n").replace("\r", "\n")
    if old_normal == new_normal and old_data != new_data:
        diagnostics.append(Diagnostic("BLOCKED", "PURE_TEXT_REWRITE", path, "内容未变，疑似仅重写编码或换行"))
        return diagnostics

    if old.eol not in {"none", new.eol} and new.eol != old.eol:
        diagnostics.append(Diagnostic("BLOCKED", "EOL_CHANGED", path, f"换行类型发生变化：{old.eol} -> {new.eol}"))
    if old.final_newline != new.final_newline:
        diagnostics.append(Diagnostic("WARNING", "FINAL_NEWLINE_CHANGED", path, "文件末尾换行状态发生变化"))

    old_parts = _line_parts(old.text)
    new_parts = _line_parts(new.text)
    matcher = difflib.SequenceMatcher(
        None,
        [part[0] for part in old_parts],
        [part[0] for part in new_parts],
        autojunk=False,
    )
    for old_start, new_start, size in matcher.get_matching_blocks():
        if any(old_parts[old_start + offset][1] != new_parts[new_start + offset][1] for offset in range(size)):
            diagnostics.append(Diagnostic("BLOCKED", "UNCHANGED_EOL_CHANGED", path, "未修改行的换行符发生变化"))
            break
    return diagnostics


def check_new(path: str, data: bytes) -> list[Diagnostic]:
    """检查新增文本文件的默认跨平台规范。"""
    suffix = pathlib.PurePosixPath(path).suffix.lower()
    if not is_text_path(path):
        return []
    info = inspect_bytes(data)
    if info.binary:
        return [Diagnostic("BLOCKED", "BINARY_SOURCE", path, "源码或配置文件被识别为二进制")]
    if info.error:
        return [Diagnostic("BLOCKED", "UNKNOWN_ENCODING", path, info.error)]
    if suffix == ".ps1":
        return _check_new_powershell(path, info)
    if suffix in TOOL_TEXT_SUFFIXES:
        if info.encoding != "utf-8":
            return [Diagnostic("WARNING", "NEW_ENCODING_REVIEW", path, f"工具文件建议使用 UTF-8，当前为 {info.encoding}")]
        if info.eol == "mixed":
            return [Diagnostic("BLOCKED", "NEW_EOL", path, "工具文件不能混用 LF 和 CRLF")]
        if info.bom not in {"none", "utf-8"}:
            return [Diagnostic("WARNING", "NEW_BOM_REVIEW", path, f"工具文件 BOM 需要人工确认：{info.bom}")]
        return []
    expected_bom = "utf-8" if suffix in {".rc", ".rc2"} else "none"
    expected_eol = "crlf" if suffix in {".bat", ".cmd"} else "lf"
    diagnostics: list[Diagnostic] = []
    if info.encoding != "utf-8":
        diagnostics.append(Diagnostic("BLOCKED", "NEW_ENCODING", path, f"新增文件必须使用 UTF-8，当前为 {info.encoding}"))
    if info.bom != expected_bom:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_BOM", path, f"BOM 应为 {expected_bom}，当前为 {info.bom}"))
    if info.eol not in {"none", expected_eol}:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_EOL", path, f"换行应为 {expected_eol}，当前为 {info.eol}"))
    if info.text and not info.final_newline:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_FINAL_NEWLINE", path, "新增文本文件必须以换行结束"))
    if info.text and "\ufffd" in info.text:
        diagnostics.append(Diagnostic("BLOCKED", "REPLACEMENT_CHARACTER", path, "包含 U+FFFD 替换字符"))
    return diagnostics


def _check_new_powershell(path: str, info: TextInfo) -> list[Diagnostic]:
    """按 PowerShell 运行目标检查新增脚本的 BOM 和换行。"""
    diagnostics: list[Diagnostic] = []
    if info.encoding != "utf-8":
        diagnostics.append(
            Diagnostic("BLOCKED", "NEW_ENCODING", path, f"新增 PowerShell 脚本必须使用 UTF-8，当前为 {info.encoding}")
        )
    if info.eol == "mixed":
        diagnostics.append(Diagnostic("BLOCKED", "NEW_EOL", path, "PowerShell 脚本不能混用 LF 和 CRLF"))
    elif info.eol not in {"none", "lf"}:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_EOL", path, "新增 PowerShell 脚本默认使用 LF 换行"))
    if info.bom not in {"none", "utf-8"}:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_BOM", path, f"PowerShell 脚本 BOM 只能是 none 或 utf-8，当前为 {info.bom}"))
    if info.text and not info.final_newline:
        diagnostics.append(Diagnostic("BLOCKED", "NEW_FINAL_NEWLINE", path, "新增 PowerShell 脚本必须以换行结束"))
    if info.text and "\ufffd" in info.text:
        diagnostics.append(Diagnostic("BLOCKED", "REPLACEMENT_CHARACTER", path, "PowerShell 脚本包含 U+FFFD 替换字符"))

    if platform.system() == "Windows" and info.text and info.bom == "none":
        has_non_ascii = any(ord(char) > 127 for char in info.text)
        if has_non_ascii and not shutil.which("pwsh"):
            diagnostics.append(
                Diagnostic(
                    "BLOCKED",
                    "PS5_BOM_REQUIRED",
                    path,
                    "当前未找到 PowerShell 7；含中文的脚本需使用 UTF-8 BOM，或先安装并使用 pwsh",
                )
            )
        elif has_non_ascii:
            diagnostics.append(
                Diagnostic(
                    "WARNING",
                    "PS5_BOM_COMPATIBILITY",
                    path,
                    "脚本含非 ASCII 字符；若明确使用 Windows PowerShell 5.1，请改为 UTF-8 BOM，否则优先使用 pwsh",
                )
            )
    if platform.system() != "Windows" and info.bom == "utf-8" and info.text and info.text.startswith("#!"):
        diagnostics.append(Diagnostic("BLOCKED", "SHEBANG_BOM", path, "Unix shebang PowerShell 脚本不能带 UTF-8 BOM"))
    return diagnostics


def _decode_paths(output: bytes) -> list[str]:
    """解码 Git NUL 分隔路径并保留非法本地字节。"""
    return [item.decode("utf-8", errors="surrogateescape") for item in output.split(b"\0") if item]


def _changed_entries(repo: pathlib.Path, staged: bool) -> list[tuple[str, str, Optional[str]]]:
    """读取新增、修改和重命名记录。"""
    arguments = ["diff", "--name-status", "-z", "-M", "--diff-filter=AMR"]
    if staged:
        arguments.insert(1, "--cached")
    fields = _decode_paths(run_git(repo, arguments))
    entries: list[tuple[str, str, Optional[str]]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith("R"):
            old_path, new_path = fields[index], fields[index + 1]
            entries.append(("R", new_path, old_path))
            index += 2
        else:
            entries.append((status[:1], fields[index], None))
            index += 1
    return entries


def _blob_from_tree(repo: pathlib.Path, revision: str, path: str) -> Optional[bytes]:
    """按对象 ID 读取树中的 blob，避免依赖工作区编码。"""
    output = run_git(repo, ["ls-tree", "-z", revision, "--", path], check=False)
    record = output.split(b"\0", 1)[0]
    if not record or b"\t" not in record:
        return None
    metadata, _ = record.split(b"\t", 1)
    fields = metadata.split()
    if len(fields) < 3 or fields[1] != b"blob" or fields[0] in {b"120000", b"160000"}:
        return None
    return run_git(repo, ["cat-file", "blob", fields[2].decode("ascii")])


def _blob_from_index(repo: pathlib.Path, path: str) -> Optional[bytes]:
    """按对象 ID 读取暂存区 blob。"""
    output = run_git(repo, ["ls-files", "--stage", "-z", "--", path], check=False)
    for record in output.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, _ = record.split(b"\t", 1)
        fields = metadata.split()
        if len(fields) >= 3 and fields[2] == b"0" and fields[0] not in {b"120000", b"160000"}:
            return run_git(repo, ["cat-file", "blob", fields[1].decode("ascii")])
    return None


def check_changes(repo: pathlib.Path, staged: bool, include_untracked: bool = True) -> list[Diagnostic]:
    """检查暂存区或工作区变更。"""
    diagnostics: list[Diagnostic] = []
    unborn = staged and subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    ).returncode != 0
    for status, path, old_path in _changed_entries(repo, staged):
        if not is_text_path(path):
            continue
        new_data = _blob_from_index(repo, path) if staged else _read_worktree(repo, path)
        if new_data is None:
            continue
        if status == "A":
            # 老项目首次建立 Git 基线时无法区分历史文件和新增文件，先保留原始字节；
            # 首次提交完成后，后续新增文件才执行新文件规范检查。
            new_diagnostics = check_new(path, new_data)
            if unborn:
                new_diagnostics = [
                    dataclasses.replace(item, level="WARNING", code="INITIAL_" + item.code)
                    if item.level == "BLOCKED" else item
                    for item in new_diagnostics
                ]
            diagnostics.extend(new_diagnostics)
            if staged:
                # Git 属性可能在索引和工作区之间做 clean/smudge；新增文件同时检查工作区字节，
                # 防止 CRLF 被规范化后掩盖实际保存格式。
                working_data = _read_worktree(repo, path)
                if working_data is not None and working_data != new_data:
                    working_diagnostics = check_new(path, working_data)
                    if unborn:
                        working_diagnostics = [
                            dataclasses.replace(item, level="WARNING", code="INITIAL_" + item.code)
                            if item.level == "BLOCKED" else item
                            for item in working_diagnostics
                        ]
                    diagnostics.extend(working_diagnostics)
            continue
        baseline_path = old_path or path
        old_data = _blob_from_tree(repo, "HEAD", baseline_path) if staged else _blob_from_index(repo, baseline_path)
        if old_data is not None:
            diagnostics.extend(compare_existing(path, old_data, new_data))

    if not staged and include_untracked:
        for path in _decode_paths(run_git(repo, ["ls-files", "--others", "--exclude-standard", "-z"])):
            data = _read_worktree(repo, path)
            if data is not None:
                diagnostics.extend(check_new(path, data))
    return _deduplicate(diagnostics)


def check_diff_size(repo: pathlib.Path, staged: bool, block_format_only: bool = False) -> list[Diagnostic]:
    """识别异常膨胀或疑似仅格式变化的单文件 diff。"""
    arguments = ["diff", "--numstat"]
    if staged:
        arguments.insert(1, "--cached")
    output = run_git(repo, arguments, check=False).decode("utf-8", errors="replace")
    diagnostics: list[Diagnostic] = []
    for line in output.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3 or fields[0] == "-" or fields[1] == "-":
            continue
        try:
            added, deleted = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        changed = added + deleted
        if changed < 200:
            continue
        path = fields[2]
        ignore_args = ["diff", "--ignore-all-space", "--numstat"]
        if staged:
            ignore_args.insert(1, "--cached")
        ignored = run_git(repo, ignore_args + ["--", path], check=False).decode("utf-8", errors="replace").strip()
        if not ignored:
            level = "BLOCKED" if block_format_only else "WARNING"
            diagnostics.append(Diagnostic(level, "FORMAT_ONLY_LARGE_DIFF", path, f"{changed} 行变化在忽略空白后消失，疑似大面积格式污染"))
        else:
            diagnostics.append(Diagnostic("WARNING", "LARGE_DIFF", path, f"单文件新增+删除 {changed} 行，需人工确认是否为必要改动"))
    return diagnostics


def _read_worktree(repo: pathlib.Path, path: str) -> Optional[bytes]:
    """读取普通工作区文件，跳过目录和符号链接。"""
    candidate = repo / pathlib.Path(path)
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        return candidate.read_bytes()
    except OSError:
        return None


def _deduplicate(items: Iterable[Diagnostic]) -> list[Diagnostic]:
    """去除同一路径的重复诊断。"""
    seen: set[tuple[str, str, str]] = set()
    result: list[Diagnostic] = []
    for item in items:
        key = (item.code, item.path, item.message)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

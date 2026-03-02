#!/usr/bin/env python3
"""dir2prompt — упаковка директории в один файл для передачи в LLM.

Архив всегда хранит полный контент. --focus влияет только на сериализацию:
  - focus-файлы идут первыми с полным контентом
  - остальные заменяются outline (структура без тел функций)
Это позволяет extract всегда восстанавливать оригиналы, а diff сравнивать
полный контент независимо от того, с каким --focus делался baseline.
"""
import ast
import os
import re
import sys
import json
import base64
import hashlib
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
FileData = Dict[str, Any]
Archive  = Dict[str, FileData]
# encoding в архиве (на диске): "utf-8" | "base64" | "skip" | "excluded" | "meta"
# encoding при сериализации:    + "outline"  (никогда не сохраняется в файл)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_text_file(file_path: str, sample_size: int = 8192) -> bool:
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(sample_size)
        if not chunk:
            return True
        if b'\x00' in chunk:
            return False
        chunk.decode('utf-8')
        return True
    except (UnicodeDecodeError, OSError):
        return False


def compile_patterns(patterns: Optional[List[str]]) -> List[re.Pattern]:
    compiled = []
    for p in (patterns or []):
        try:
            compiled.append(re.compile(p))
        except re.error as e:
            print(f"❌ Невалидная регулярка '{p}': {e}", file=sys.stderr)
            sys.exit(1)
    return compiled


def is_matched(rel_path: str, patterns: List[re.Pattern]) -> bool:
    return any(p.search(rel_path.replace(os.sep, '/')) for p in patterns)


def ask_confirmation(prompt: str) -> bool:
    return input(prompt).strip().lower() in ('y', 'yes')


def content_hash(content: str) -> str:
    return hashlib.md5(content.encode('utf-8', errors='replace')).hexdigest()


# ---------------------------------------------------------------------------
# Strip
# ---------------------------------------------------------------------------

def strip_content(text: str) -> str:
    """Trailing whitespace + схлопывает 3+ пустых строки в 2."""
    lines = [l.rstrip() for l in text.splitlines()]
    result: List[str] = []
    blanks = 0
    for line in lines:
        if line == '':
            blanks += 1
            if blanks <= 2:
                result.append('')
        else:
            blanks = 0
            result.append(line)
    return '\n'.join(result)


# ---------------------------------------------------------------------------
# Outline  (только для сериализации, никогда не сохраняется в архив)
# ---------------------------------------------------------------------------

def _func_sig(node: ast.FunctionDef) -> str:
    args = node.args
    parts: List[str] = []

    defaults_offset = len(args.args) - len(args.defaults)
    for i, arg in enumerate(args.args):
        ann  = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        dflt = f" = {ast.unparse(args.defaults[i - defaults_offset])}" \
               if i >= defaults_offset else ""
        parts.append(f"{arg.arg}{ann}{dflt}")

    if args.vararg:
        ann = f": {ast.unparse(args.vararg.annotation)}" if args.vararg.annotation else ""
        parts.append(f"*{args.vararg.arg}{ann}")
    elif args.kwonlyargs:
        parts.append("*")

    for i, arg in enumerate(args.kwonlyargs):
        ann  = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        dflt = f" = {ast.unparse(args.kw_defaults[i])}" \
               if args.kw_defaults[i] is not None else ""
        parts.append(f"{arg.arg}{ann}{dflt}")

    if args.kwarg:
        ann = f": {ast.unparse(args.kwarg.annotation)}" if args.kwarg.annotation else ""
        parts.append(f"**{args.kwarg.arg}{ann}")

    ret    = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    return f"{' ' * node.col_offset}{prefix}{node.name}({', '.join(parts)}){ret}: ..."


def make_outline_python(source: str, path: str) -> str:
    total = len(source.splitlines())
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"# OUTLINE: {path} — parse error: {e}\n" + \
               '\n'.join(source.splitlines()[:30])

    out = [f"# OUTLINE: {path} ({total} lines)"]
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = ', '.join(ast.unparse(b) for b in node.bases)
            out.append(f"\nclass {node.name}({bases}):")
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(f"    {_func_sig(child).lstrip()}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_func_sig(node))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    out.append(f"{target.id} = ...")
    return '\n'.join(out)


def make_outline_generic(source: str, path: str, head: int = 30) -> str:
    lines = source.splitlines()
    if len(lines) <= head:
        return source
    return '\n'.join(lines[:head]) + f"\n\n# ... ({len(lines)} lines total, showing {head})"


def make_outline(source: str, path: str) -> str:
    if path.endswith('.py'):
        return make_outline_python(source, path)
    return make_outline_generic(source, path)


# ---------------------------------------------------------------------------
# Collect  (всегда полный контент, без outline)
# ---------------------------------------------------------------------------

def collect_files(
    source_dir: str,
    include_hidden: bool = False,
    include_binary: bool = False,
    exclude_patterns: Optional[List[re.Pattern]] = None,
    strip: bool = False,
) -> Archive:
    """Собирает файлы. Всегда возвращает полный контент.
    encoding: "utf-8" | "base64" | "skip" | "excluded"
    """
    result: Archive = {}
    exclude_patterns = exclude_patterns or []
    source_path = Path(source_dir).resolve()

    for root, dirs, files in os.walk(source_dir):
        if not include_hidden:
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            files   = [f for f in files if not f.startswith('.')]

        for filename in sorted(files):
            full_path = os.path.join(root, filename)
            rel_path  = os.path.relpath(full_path, source_path)

            if is_matched(rel_path, exclude_patterns):
                result[rel_path] = {"encoding": "excluded", "content": None}
                continue

            try:
                if is_text_file(full_path):
                    content = Path(full_path).read_text(encoding='utf-8')
                    if strip:
                        content = strip_content(content)
                    result[rel_path] = {"encoding": "utf-8", "content": content}
                elif include_binary:
                    raw = Path(full_path).read_bytes()
                    result[rel_path] = {
                        "encoding": "base64",
                        "content":  base64.b64encode(raw).decode('ascii'),
                    }
                else:
                    result[rel_path] = {"encoding": "skip", "content": None}
            except OSError as e:
                print(f"⚠️  Warning: cannot read '{rel_path}': {e}", file=sys.stderr)
                result[rel_path] = {"encoding": "skip", "content": None}

    return result


# ---------------------------------------------------------------------------
# Focus  (применяется только при сериализации)
# ---------------------------------------------------------------------------

def apply_focus(data: Archive, focus_patterns: List[re.Pattern]) -> Archive:
    """Возвращает новый архив: focus-файлы первыми (полный контент),
    остальные текстовые — с outline. Не изменяет исходный архив.
    Если focus_patterns пуст — возвращает data без изменений.
    """
    if not focus_patterns:
        return data

    focused: Archive = {}
    rest:    Archive = {}

    for rel_path, fd in data.items():
        if is_matched(rel_path, focus_patterns):
            focused[rel_path] = fd
        elif fd.get("encoding") == "utf-8":
            content = fd["content"]
            rest[rel_path] = {
                "encoding": "outline",
                "content":  make_outline(content, rel_path),
            }
        else:
            rest[rel_path] = fd

    return {**focused, **rest}


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def serialize_json(data: Archive) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def deserialize_json(text: str) -> Archive:
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка парсинга JSON: {e}", file=sys.stderr)
        sys.exit(1)


def _xml_attr(value: str) -> str:
    return value.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;')


def serialize_xml(data: Archive) -> str:
    parts = ['<files>']
    for rel_path, fd in data.items():
        enc     = fd.get("encoding", "utf-8")
        content = fd.get("content")
        p_attr  = _xml_attr(rel_path)
        if content is None:
            parts.append(f'  <file path="{p_attr}" encoding="{enc}"/>')
        elif enc == "base64":
            parts.append(f'  <file path="{p_attr}" encoding="base64">{content}</file>')
        else:
            safe = content.replace(']]>', ']]]]><![CDATA[>')
            parts.append(
                f'  <file path="{p_attr}" encoding="{enc}"><![CDATA[\n{safe}\n]]></file>'
            )
    parts.append('</files>')
    return '\n'.join(parts)


def deserialize_xml(text: str) -> Archive:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        print(f"❌ Ошибка парсинга XML: {e}", file=sys.stderr)
        sys.exit(1)
    data: Archive = {}
    for el in root.findall('file'):
        path    = el.get('path', '')
        enc     = el.get('encoding', 'utf-8')
        content = el.text
        if content and enc not in ('base64', 'skip', 'excluded', 'meta'):
            content = content.removeprefix('\n').removesuffix('\n')
        data[path] = {"encoding": enc, "content": content}
    return data


def detect_format(file_path: str) -> str:
    p = Path(file_path)
    if p.suffix == '.xml':  return 'xml'
    if p.suffix == '.json': return 'json'
    try:
        head = p.read_text(encoding='utf-8', errors='ignore')[:64].strip()
        if head.startswith('<'): return 'xml'
    except OSError:
        pass
    return 'json'


def load_archive(file_path: str) -> Archive:
    fmt = detect_format(file_path)
    try:
        text = Path(file_path).read_text(encoding='utf-8')
    except OSError as e:
        print(f"❌ Ошибка чтения '{file_path}': {e}", file=sys.stderr)
        sys.exit(1)
    return deserialize_xml(text) if fmt == 'xml' else deserialize_json(text)


def save_archive(data: Archive, file_path: str, fmt: str) -> None:
    """Сохраняет архив. outline-записи НЕ попадают в файл — они заменяются на utf-8."""
    # Гарантируем: в файл никогда не пишем encoding=outline
    clean: Archive = {}
    for path, fd in data.items():
        if fd.get("encoding") == "outline":
            clean[path] = {"encoding": "utf-8", "content": fd["content"]}
        else:
            clean[path] = fd
    text = serialize_xml(clean) if fmt == 'xml' else serialize_json(clean)
    try:
        Path(file_path).write_text(text, encoding='utf-8')
    except OSError as e:
        print(f"❌ Ошибка записи '{file_path}': {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

ICONS = {
    "utf-8":    "📄",
    "outline":  "📋",
    "base64":   "🔷",
    "skip":     "⬜",
    "excluded": "🚫",
    "meta":     "📌",
}


def print_file_structure(data: Archive, mode: str, source: str, dest: str) -> None:
    print(f"\n📁 Операция:   {mode}")
    print(f"📂 Источник:   {source}")
    print(f"📂 Назначение: {dest}")
    print("\n📋 Структура файлов:")

    root_files: List[Tuple[str, FileData]] = []
    dirs: Dict[str, List[Tuple[str, FileData]]] = {}

    for rel_path in data:
        parts = Path(rel_path).parts
        if len(parts) == 1:
            root_files.append((rel_path, data[rel_path]))
        else:
            dirs.setdefault(parts[0], []).append((rel_path, data[rel_path]))

    for rel_path, fd in root_files:
        enc = fd.get("encoding", "utf-8")
        print(f"  {ICONS.get(enc, '📄')} {rel_path}")
    for dir_name in sorted(dirs):
        print(f"  📁 {dir_name}/")
        for rel_path, fd in dirs[dir_name]:
            enc = fd.get("encoding", "utf-8")
            print(f"    {ICONS.get(enc, '📄')} {os.path.basename(rel_path)}")

    counts: Dict[str, int] = {}
    total_chars = 0
    for fd in data.values():
        enc = fd.get("encoding", "utf-8")
        counts[enc] = counts.get(enc, 0) + 1
        if fd.get("content"):
            total_chars += len(fd["content"])

    labels = {
        "utf-8":    "Текстовых (полный)",
        "outline":  "Outline",
        "base64":   "Бинарных (base64)",
        "skip":     "Бинарных (пропущено)",
        "excluded": "Исключённых",
    }
    print(f"\n📊 Статистика:")
    for key, label in labels.items():
        if counts.get(key):
            print(f"   {label}: {counts[key]}")
    print(f"   Всего файлов: {len(data)}")
    print(f"   ~Токенов:     {total_chars // 4:,}")


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def extract_from_archive(
    data: Archive,
    source_label: str,
    target_dir: str,
    overwrite_all: bool = False,
    skip_confirmation: bool = False,
    exclude_patterns: Optional[List[re.Pattern]] = None,
) -> None:
    exclude_patterns = exclude_patterns or []
    print_file_structure(data, "EXTRACT", source_label, target_dir)

    if not skip_confirmation:
        if not ask_confirmation("\n❓ Продолжить восстановление? [y/N] "):
            print("⏹️  Отменено.")
            return
    print()

    for rel_path, fd in sorted(data.items()):
        enc     = fd.get("encoding", "utf-8")
        content = fd.get("content")

        if enc in ("skip", "excluded", "meta") or content is None:
            label = {"excluded": "исключён", "meta": "мета"}.get(enc, "пропущен")
            print(f"⏭️  {label}: {rel_path}")
            continue

        if is_matched(rel_path, exclude_patterns):
            print(f"🚫 Исключён (--exclude): {rel_path}")
            continue

        target_path = Path(target_dir) / rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if target_path.exists() and not overwrite_all:
            if not ask_confirmation(f"Перезаписать '{rel_path}'? [y/N] "):
                print(f"⏭️  Пропущен: {rel_path}")
                continue
            print(f"🔄 Перезапись: {rel_path}")

        try:
            if enc == "base64":
                target_path.write_bytes(base64.b64decode(content))
            else:
                # enc может быть "utf-8" или "outline" (из старых архивов) — пишем как текст
                target_path.write_text(content, encoding='utf-8')
            print(f"✅ Восстановлен: {rel_path}")
        except (OSError, ValueError) as e:
            print(f"❌ Ошибка записи '{rel_path}': {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Diff  (сравнивает полный контент, --focus не нужен)
# ---------------------------------------------------------------------------

def build_diff(
    source_dir: str,
    baseline: Archive,
    include_hidden: bool = False,
    include_binary: bool = False,
    exclude_patterns: Optional[List[re.Pattern]] = None,
    strip: bool = False,
) -> Tuple[Archive, Dict[str, List[str]]]:
    """Сравнивает файловую систему с baseline по полному контенту.
    --focus не влияет на сравнение — только на финальную сериализацию.
    """
    current = collect_files(
        source_dir,
        include_hidden=include_hidden,
        include_binary=include_binary,
        exclude_patterns=exclude_patterns,
        strip=strip,
    )

    # Нормализуем baseline: outline → полный контент уже там, просто берём как есть
    def baseline_hash(path: str) -> Optional[str]:
        fd = baseline.get(path)
        if fd is None:
            return None
        content = fd.get("content")
        if content is None:
            return None
        return content_hash(content)

    added:    List[str] = []
    modified: List[str] = []
    deleted:  List[str] = []
    diff:     Archive   = {}

    for path, fd in current.items():
        enc     = fd.get("encoding")
        content = fd.get("content") or ""
        if enc in ("skip", "excluded"):
            continue
        bh = baseline_hash(path)
        if bh is None:
            added.append(path)
            diff[path] = fd
        elif content_hash(content) != bh:
            modified.append(path)
            diff[path] = fd

    for path in baseline:
        if path.startswith("__"):
            continue
        if baseline[path].get("encoding") in ("skip", "excluded", "meta"):
            continue
        if path not in current:
            deleted.append(path)

    return diff, {
        "added":    sorted(added),
        "modified": sorted(modified),
        "deleted":  sorted(deleted),
    }


def print_diff_summary(summary: Dict[str, List[str]]) -> None:
    added    = summary["added"]
    modified = summary["modified"]
    deleted  = summary["deleted"]
    print("\n📊 Сводка изменений:")
    if added:
        print(f"  ✅ Добавлено ({len(added)}):")
        for p in added: print(f"     + {p}")
    if modified:
        print(f"  🔄 Изменено ({len(modified)}):")
        for p in modified: print(f"     ~ {p}")
    if deleted:
        print(f"  ❌ Удалено ({len(deleted)}):")
        for p in deleted: print(f"     - {p}")
    if not added and not modified and not deleted:
        print("  ✨ Изменений нет")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--exclude", "-e", metavar="REGEX", action="append", default=[],
        help="Исключить файлы по регулярке (повторяемый флаг)",
    )
    p.add_argument("--yes", "-y", action="store_true", help="Не запрашивать подтверждение")
    p.add_argument("--hidden", "-H", action="store_true", help="Включить скрытые файлы/папки")


def _add_pack_args(p: argparse.ArgumentParser, with_focus: bool = True) -> None:
    p.add_argument(
        "--format", "-f", choices=["xml", "json"], default="xml",
        help="Формат вывода: xml (по умолчанию) или json",
    )
    p.add_argument("--binary", "-b", action="store_true",
                   help="Кодировать бинарные файлы в base64")
    p.add_argument("--strip", "-s", action="store_true",
                   help="Убрать trailing whitespace и лишние пустые строки")
    if with_focus:
        p.add_argument(
            "--focus", metavar="REGEX", action="append", default=[],
            help=(
                "Focus-файлы идут первыми с полным контентом, "
                "остальные — outline (повторяемый флаг). "
                "Не влияет на содержимое архива, только на порядок и отображение."
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Упаковка директории в один файл для передачи в LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
примеры:
  archive ./src out.xml
  archive ./src out.xml --focus 'models\\.py$' --strip -e '^dist/'
  archive ./src out.json --format json --binary
  extract out.xml ./restored
  diff ./src baseline.xml delta.xml            # --focus не нужен
  estimate ./src --focus 'core\\.py$'
        """,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    ap = sub.add_parser("archive", help="Упаковать директорию в архив")
    ap.add_argument("source_dir")
    ap.add_argument("output_file")
    _add_pack_args(ap, with_focus=True)
    _add_common_args(ap)

    ep = sub.add_parser("extract", help="Восстановить файлы из архива")
    ep.add_argument("archive_file")
    ep.add_argument("target_dir")
    _add_common_args(ep)

    dp = sub.add_parser("diff", help="Архив только из изменённых файлов")
    dp.add_argument("source_dir")
    dp.add_argument("baseline_file", help="Предыдущий архив (.xml или .json)")
    dp.add_argument("output_file")
    _add_pack_args(dp, with_focus=False)   # --focus не нужен в diff
    _add_common_args(dp)

    estp = sub.add_parser("estimate", help="Оценить размер в токенах без сохранения")
    estp.add_argument("source_dir")
    estp.add_argument("--binary", "-b", action="store_true")
    estp.add_argument("--strip",  "-s", action="store_true")
    estp.add_argument("--focus", metavar="REGEX", action="append", default=[],
                      help="Смоделировать --focus для оценки экономии токенов")
    _add_common_args(estp)

    args = parser.parse_args()
    exclude_patterns = compile_patterns(args.exclude)

    # ================================================================ ARCHIVE
    if args.mode == "archive":
        if not os.path.isdir(args.source_dir):
            print(f"❌ '{args.source_dir}' не является директорией", file=sys.stderr)
            sys.exit(1)

        focus_patterns = compile_patterns(args.focus)
        data = collect_files(
            args.source_dir,
            include_hidden=args.hidden,
            include_binary=args.binary,
            exclude_patterns=exclude_patterns,
            strip=args.strip,
        )
        display_data = apply_focus(data, focus_patterns)
        print_file_structure(display_data, "ARCHIVE", args.source_dir, args.output_file)

        if not args.yes:
            if not ask_confirmation("\n❓ Продолжить? [y/N] "):
                print("⏹️  Отменено.")
                sys.exit(0)

        # Сохраняем data (полный контент), а не display_data (с outline)
        save_archive(data, args.output_file, args.format)
        print(f"\n✅ Архив: {args.output_file}  [{args.format.upper()}]")
        print(f"📁 Файлов: {len(data)}")

    # ================================================================ EXTRACT
    elif args.mode == "extract":
        if not os.path.isfile(args.archive_file):
            print(f"❌ '{args.archive_file}' не найден", file=sys.stderr)
            sys.exit(1)
        Path(args.target_dir).mkdir(parents=True, exist_ok=True)
        data = load_archive(args.archive_file)
        extract_from_archive(
            data, args.archive_file, args.target_dir,
            overwrite_all=args.yes,
            skip_confirmation=args.yes,
            exclude_patterns=exclude_patterns,
        )

    # ================================================================ DIFF
    elif args.mode == "diff":
        if not os.path.isdir(args.source_dir):
            print(f"❌ '{args.source_dir}' не является директорией", file=sys.stderr)
            sys.exit(1)
        if not os.path.isfile(args.baseline_file):
            print(f"❌ baseline '{args.baseline_file}' не найден", file=sys.stderr)
            sys.exit(1)

        baseline      = load_archive(args.baseline_file)
        diff, summary = build_diff(
            args.source_dir, baseline,
            include_hidden=args.hidden,
            include_binary=args.binary,
            exclude_patterns=exclude_patterns,
            strip=args.strip,
        )
        print_diff_summary(summary)

        if not diff:
            print("\n✨ Изменений нет — файл не сохранён.")
            sys.exit(0)

        print_file_structure(diff, "DIFF", args.source_dir, args.output_file)

        if not args.yes:
            if not ask_confirmation("\n❓ Сохранить diff? [y/N] "):
                print("⏹️  Отменено.")
                sys.exit(0)

        if summary["deleted"]:
            diff["__deleted__"] = {
                "encoding": "meta",
                "content":  '\n'.join(summary["deleted"]),
            }
        save_archive(diff, args.output_file, args.format)
        print(f"\n✅ Diff: {args.output_file}  [{args.format.upper()}]")

    # ================================================================ ESTIMATE
    elif args.mode == "estimate":
        if not os.path.isdir(args.source_dir):
            print(f"❌ '{args.source_dir}' не является директорией", file=sys.stderr)
            sys.exit(1)

        focus_patterns = compile_patterns(args.focus)
        data = collect_files(
            args.source_dir,
            include_hidden=args.hidden,
            include_binary=args.binary,
            exclude_patterns=exclude_patterns,
            strip=args.strip,
        )
        display_data = apply_focus(data, focus_patterns)
        print_file_structure(display_data, "ESTIMATE", args.source_dir, "(не сохраняется)")

        json_sz = len(serialize_json(display_data).encode('utf-8'))
        xml_sz  = len(serialize_xml(display_data).encode('utf-8'))
        print(f"\n💾 Размер при сериализации:")
        print(f"   JSON: {json_sz:>10,} байт  (~{json_sz // 4:,} токенов)")
        print(f"   XML:  {xml_sz:>10,} байт  (~{xml_sz // 4:,} токенов)")


if __name__ == "__main__":
    main()

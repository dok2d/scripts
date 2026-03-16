#!/usr/bin/env python3
"""
Firefox Bookmarks Manager
=========================
Инструмент для работы с экспортированными закладками Firefox.

Возможности:
  1. Экспорт отдельной папки закладок в отдельный файл (HTML или JSON)
  2. Проверка доступности закладок и удаление мёртвых ссылок

Поддерживает оба формата экспорта Firefox: HTML и JSON.

Использование:
  python firefox_bookmarks_tool.py <команда> [аргументы]

Команды:
  list-folders   — показать структуру папок
  export-folder  — экспортировать папку в отдельный файл
  check-alive    — проверить доступность и создать чистый файл
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import ssl
import socket
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional


# ─────────────────────────────────────────────
#  HTML Parser for Firefox bookmark exports
# ─────────────────────────────────────────────

class BookmarkHTMLParser(HTMLParser):
    """Парсит HTML-файл закладок Firefox в древовидную структуру."""

    def __init__(self):
        super().__init__()
        self.root = {"type": "folder", "title": "Root", "children": []}
        self.stack = [self.root]
        self.current_tag = None
        self.current_attrs = {}
        self.in_h3 = False
        self.in_a = False
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)

        if tag == "dl":
            # Новый уровень вложенности — привязываем к последней папке
            pass
        elif tag == "dt":
            pass
        elif tag == "h3":
            self.in_h3 = True
            self.current_text = ""
            self.current_attrs = attrs_dict
        elif tag == "a":
            self.in_a = True
            self.current_text = ""
            self.current_attrs = attrs_dict

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "h3" and self.in_h3:
            self.in_h3 = False
            folder = {
                "type": "folder",
                "title": self.current_text.strip(),
                "children": [],
                "attrs": self.current_attrs,
            }
            self.stack[-1]["children"].append(folder)
            self.stack.append(folder)

        elif tag == "a" and self.in_a:
            self.in_a = False
            href = self.current_attrs.get("href", "")
            bookmark = {
                "type": "bookmark",
                "title": self.current_text.strip(),
                "url": href,
                "attrs": self.current_attrs,
            }
            self.stack[-1]["children"].append(bookmark)

        elif tag == "dl":
            if len(self.stack) > 1:
                self.stack.pop()

    def handle_data(self, data):
        if self.in_h3 or self.in_a:
            self.current_text += data


def parse_html_bookmarks(filepath: str) -> dict:
    """Читает HTML-файл закладок и возвращает дерево."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    parser = BookmarkHTMLParser()
    parser.feed(content)
    return parser.root


# ─────────────────────────────────────────────
#  JSON Parser for Firefox bookmark exports
# ─────────────────────────────────────────────

def parse_json_bookmarks(filepath: str) -> dict:
    """Читает JSON-файл закладок Firefox и возвращает унифицированное дерево."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _normalize_json_node(data)


def _normalize_json_node(node: dict) -> dict:
    """Приводит узел JSON-закладок к общему формату."""
    if node.get("type") == "text/x-moz-place-container" or "children" in node:
        children = [_normalize_json_node(c) for c in node.get("children", [])]
        return {
            "type": "folder",
            "title": node.get("title", ""),
            "children": children,
            "raw": {k: v for k, v in node.items() if k != "children"},
        }
    else:
        return {
            "type": "bookmark",
            "title": node.get("title", ""),
            "url": node.get("uri", ""),
            "raw": node,
        }


# ─────────────────────────────────────────────
#  Auto-detect format & load
# ─────────────────────────────────────────────

def load_bookmarks(filepath: str) -> tuple[dict, str]:
    """
    Загружает закладки, автоматически определяя формат.
    Возвращает (дерево, формат: 'html' | 'json').
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        head = f.read(512).strip()

    if head.startswith("{") or head.startswith("["):
        return parse_json_bookmarks(filepath), "json"
    else:
        return parse_html_bookmarks(filepath), "html"


# ─────────────────────────────────────────────
#  List folders
# ─────────────────────────────────────────────

def list_folders(node: dict, prefix: str = "", path: str = "") -> list[str]:
    """Рекурсивно собирает пути всех папок."""
    results = []
    if node["type"] == "folder":
        current_path = f"{path}/{node['title']}" if path else node["title"]
        bm_count = _count_bookmarks(node)
        results.append(f"{prefix}{current_path}  ({bm_count} закладок)")
        for child in node.get("children", []):
            results.extend(list_folders(child, prefix, current_path))
    return results


def _count_bookmarks(node: dict) -> int:
    """Считает все закладки (рекурсивно) в узле."""
    if node["type"] == "bookmark":
        return 1
    return sum(_count_bookmarks(c) for c in node.get("children", []))


# ─────────────────────────────────────────────
#  Find folder by path
# ─────────────────────────────────────────────

def find_folder(node: dict, target_path: str) -> Optional[dict]:
    """
    Ищет папку по пути вида 'Root/Панель закладок/Dev'.
    Поиск нечувствителен к регистру.
    """
    parts = [p.strip() for p in target_path.strip("/").split("/") if p.strip()]
    return _find_folder_recursive(node, parts)


def _find_folder_recursive(node: dict, parts: list[str]) -> Optional[dict]:
    if not parts:
        return node
    if node["type"] != "folder":
        return None
    target = parts[0].lower()
    for child in node.get("children", []):
        if child["type"] == "folder" and child["title"].lower() == target:
            result = _find_folder_recursive(child, parts[1:])
            if result:
                return result
    return None


# ─────────────────────────────────────────────
#  Export to HTML
# ─────────────────────────────────────────────

def export_to_html(node: dict, filepath: str):
    """Экспортирует дерево закладок в HTML-формат Firefox."""
    lines = []
    lines.append("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    lines.append("<!-- This is an automatically generated file.")
    lines.append("     It will be parsed to restore your bookmarks. -->")
    lines.append('<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">')
    lines.append(f"<TITLE>Bookmarks</TITLE>")
    lines.append("<H1>Bookmarks</H1>")
    lines.append("")
    _write_html_node(node, lines, indent=0)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _write_html_node(node: dict, lines: list[str], indent: int):
    pad = "    " * indent

    if node["type"] == "folder":
        attrs_str = ""
        if "attrs" in node:
            for k, v in node["attrs"].items():
                attrs_str += f' {k.upper()}="{_html_escape(v)}"'
        lines.append(f"{pad}<DT><H3{attrs_str}>{_html_escape(node['title'])}</H3>")
        lines.append(f"{pad}<DL><p>")
        for child in node.get("children", []):
            _write_html_node(child, lines, indent + 1)
        lines.append(f"{pad}</DL><p>")

    elif node["type"] == "bookmark":
        attrs = node.get("attrs", {})
        href = node.get("url", "")
        attr_parts = [f'HREF="{_html_escape(href)}"']
        for k, v in attrs.items():
            if k.lower() in ("href",):
                continue
            attr_parts.append(f'{k.upper()}="{_html_escape(v)}"')
        attrs_str = " ".join(attr_parts)
        lines.append(f"{pad}<DT><A {attrs_str}>{_html_escape(node['title'])}</A>")


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ─────────────────────────────────────────────
#  Export to JSON
# ─────────────────────────────────────────────

def export_to_json(node: dict, filepath: str):
    """Экспортирует дерево закладок в JSON-формат Firefox."""
    json_tree = _to_firefox_json(node)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(json_tree, f, ensure_ascii=False, indent=2)


def _to_firefox_json(node: dict) -> dict:
    if node["type"] == "folder":
        base = node.get("raw", {}).copy()
        base["title"] = node["title"]
        base["type"] = "text/x-moz-place-container"
        base["children"] = [_to_firefox_json(c) for c in node.get("children", [])]
        return base
    else:
        base = node.get("raw", {}).copy()
        base["title"] = node["title"]
        base["uri"] = node.get("url", "")
        base["type"] = "text/x-moz-place"
        return base


# ─────────────────────────────────────────────
#  URL Checking
# ─────────────────────────────────────────────

def check_url(url: str, timeout: int = 10) -> tuple[str, bool, str]:
    """
    Проверяет доступность URL.
    Возвращает (url, is_alive, reason).
    """
    # Пропускаем внутренние URL Firefox
    if url and url.startswith(("about:", "chrome://", "file://", "place:", "javascript:")):
        return url, True, "внутренний URL"

    if not url or not url.startswith(("http://", "https://")):
        return url, False, "не HTTP(S)"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": "Mozilla/5.0 (compatible; BookmarkChecker/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            code = resp.getcode()
            if code < 400:
                return url, True, f"HTTP {code}"
            else:
                return url, False, f"HTTP {code}"
    except urllib.error.HTTPError as e:
        # Некоторые сайты блокируют HEAD, пробуем GET
        if e.code in (405, 403, 401):
            try:
                req2 = urllib.request.Request(
                    url,
                    method="GET",
                    headers={"User-Agent": "Mozilla/5.0 (compatible; BookmarkChecker/1.0)"},
                )
                with urllib.request.urlopen(req2, timeout=timeout, context=ctx) as resp2:
                    return url, resp2.getcode() < 400, f"HTTP {resp2.getcode()}"
            except Exception:
                pass
        if e.code in (401, 403):
            # Сайт жив, просто требует авторизацию
            return url, True, f"HTTP {e.code} (требует авторизацию)"
        return url, False, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        reason = str(e)
        if "Name or service not known" in reason or "getaddrinfo failed" in reason:
            return url, False, "домен не найден"
        elif "timed out" in reason or "timeout" in reason.lower():
            return url, False, "таймаут"
        elif "Connection refused" in reason:
            return url, False, "соединение отклонено"
        elif "SSL" in reason or "certificate" in reason.lower():
            return url, True, "ошибка SSL (сайт, вероятно, жив)"
        return url, False, reason[:80]
    except Exception as e:
        return url, False, str(e)[:80]


def collect_all_urls(node: dict) -> list[tuple[str, dict]]:
    """Собирает все закладки (url, node) из дерева."""
    results = []
    if node["type"] == "bookmark" and node.get("url"):
        results.append((node["url"], node))
    for child in node.get("children", []):
        results.extend(collect_all_urls(child))
    return results


def filter_dead_bookmarks(node: dict, dead_urls: set[str]) -> Optional[dict]:
    """
    Возвращает копию дерева без мёртвых закладок.
    Пустые папки тоже удаляются.
    """
    if node["type"] == "bookmark":
        if node.get("url", "") in dead_urls:
            return None
        return dict(node)

    # Folder
    new_children = []
    for child in node.get("children", []):
        filtered = filter_dead_bookmarks(child, dead_urls)
        if filtered is not None:
            new_children.append(filtered)

    if not new_children and node.get("title") not in ("Root", ""):
        return None

    new_node = dict(node)
    new_node["children"] = new_children
    return new_node


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────

def cmd_list_folders(args):
    tree, fmt = load_bookmarks(args.input)
    print(f"Формат файла: {fmt.upper()}")
    print(f"{'=' * 60}")
    folders = list_folders(tree)
    for f in folders:
        print(f)
    print(f"{'=' * 60}")
    total = _count_bookmarks(tree)
    print(f"Всего закладок: {total}")


def cmd_export_folder(args):
    tree, src_fmt = load_bookmarks(args.input)
    folder = find_folder(tree, args.folder)

    if not folder:
        print(f"Ошибка: папка '{args.folder}' не найдена.", file=sys.stderr)
        print("Доступные папки:", file=sys.stderr)
        for f in list_folders(tree):
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)

    out_fmt = args.format or src_fmt
    output = args.output
    if not output:
        safe_name = re.sub(r'[^\w\-]', '_', folder["title"])
        output = f"{safe_name}_bookmarks.{out_fmt}"

    if out_fmt == "html":
        export_to_html(folder, output)
    else:
        export_to_json(folder, output)

    count = _count_bookmarks(folder)
    print(f"Экспортировано: {count} закладок из папки '{folder['title']}'")
    print(f"Файл: {output} ({out_fmt.upper()})")


def cmd_check_alive(args):
    tree, src_fmt = load_bookmarks(args.input)
    all_bookmarks = collect_all_urls(tree)

    total = len(all_bookmarks)
    print(f"Найдено {total} закладок для проверки.")
    print(f"Параллельных потоков: {args.threads}")
    print(f"Таймаут: {args.timeout} сек.")
    print()

    dead_urls = set()
    alive_count = 0
    checked = 0

    unique_urls = {}
    for url, node in all_bookmarks:
        if url not in unique_urls:
            unique_urls[url] = node
    unique_list = list(unique_urls.keys())

    print(f"Уникальных URL: {len(unique_list)}")
    print()

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = {
            executor.submit(check_url, url, args.timeout): url
            for url in unique_list
        }

        for future in as_completed(futures):
            url, is_alive, reason = future.result()
            checked += 1

            if is_alive:
                alive_count += 1
                status = "✓"
            else:
                dead_urls.add(url)
                status = "✗"

            # Прогресс
            if checked % 10 == 0 or not is_alive:
                pct = checked / len(unique_list) * 100
                title = unique_urls[url].get("title", "")[:40]
                if not is_alive:
                    print(f"  [{checked}/{len(unique_list)} {pct:.0f}%] {status} {reason:30s} {title}")
                else:
                    print(f"  [{checked}/{len(unique_list)} {pct:.0f}%] проверено...", end="\r", flush=True)

    print(f"\n{'=' * 60}")
    print(f"Результаты:")
    print(f"  Всего проверено: {len(unique_list)}")
    print(f"  Живых:           {alive_count}")
    print(f"  Мёртвых:         {len(dead_urls)}")

    if dead_urls:
        # Сохраняем отчёт о мёртвых ссылках
        report_path = args.report or "dead_bookmarks_report.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"Отчёт о недоступных закладках — {datetime.now().isoformat()}\n")
            f.write(f"{'=' * 60}\n\n")
            for url, node in all_bookmarks:
                if url in dead_urls:
                    f.write(f"  Название: {node.get('title', '—')}\n")
                    f.write(f"  URL:      {url}\n\n")
        print(f"\n  Отчёт о мёртвых ссылках: {report_path}")

    if not args.no_clean:
        cleaned = filter_dead_bookmarks(tree, dead_urls)
        out_fmt = args.format or src_fmt
        output = args.output
        if not output:
            base = os.path.splitext(os.path.basename(args.input))[0]
            output = f"{base}_clean.{out_fmt}"

        if out_fmt == "html":
            export_to_html(cleaned, output)
        else:
            export_to_json(cleaned, output)

        clean_count = _count_bookmarks(cleaned)
        print(f"  Чистый файл:             {output}")
        print(f"  Закладок в чистом файле: {clean_count}")


def main():
    parser = argparse.ArgumentParser(
        description="Firefox Bookmarks Manager — управление экспортированными закладками",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:

  # Показать все папки
  python firefox_bookmarks_tool.py list-folders -i bookmarks.html

  # Экспортировать папку «Dev» в отдельный файл
  python firefox_bookmarks_tool.py export-folder -i bookmarks.html -f "Root/Панель закладок/Dev"

  # Проверить все закладки и создать чистый файл без мёртвых
  python firefox_bookmarks_tool.py check-alive -i bookmarks.html

  # То же, но с JSON и 20 потоками
  python firefox_bookmarks_tool.py check-alive -i bookmarks.json --threads 20
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Команда")

    # list-folders
    p_list = subparsers.add_parser("list-folders", help="Показать структуру папок")
    p_list.add_argument("-i", "--input", required=True, help="Файл закладок (HTML или JSON)")

    # export-folder
    p_export = subparsers.add_parser("export-folder", help="Экспортировать папку в отдельный файл")
    p_export.add_argument("-i", "--input", required=True, help="Файл закладок (HTML или JSON)")
    p_export.add_argument("-f", "--folder", required=True, help="Путь к папке (например: Root/Toolbar/Dev)")
    p_export.add_argument("-o", "--output", help="Имя выходного файла")
    p_export.add_argument("--format", choices=["html", "json"], help="Формат выходного файла (по умолчанию — как исходный)")

    # check-alive
    p_check = subparsers.add_parser("check-alive", help="Проверить доступность закладок")
    p_check.add_argument("-i", "--input", required=True, help="Файл закладок (HTML или JSON)")
    p_check.add_argument("-o", "--output", help="Имя чистого выходного файла")
    p_check.add_argument("--format", choices=["html", "json"], help="Формат выходного файла")
    p_check.add_argument("--threads", type=int, default=10, help="Число параллельных потоков (по умолчанию: 10)")
    p_check.add_argument("--timeout", type=int, default=10, help="Таймаут запроса в секундах (по умолчанию: 10)")
    p_check.add_argument("--report", help="Путь к файлу отчёта о мёртвых ссылках")
    p_check.add_argument("--no-clean", action="store_true", help="Не создавать чистый файл (только отчёт)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "list-folders":
        cmd_list_folders(args)
    elif args.command == "export-folder":
        cmd_export_folder(args)
    elif args.command == "check-alive":
        cmd_check_alive(args)


if __name__ == "__main__":
    main()

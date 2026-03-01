#!/usr/bin/env python3
import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, Optional

def is_text_file(file_path: str, sample_size: int = 4096) -> bool:
    """Проверяет, является ли файл текстовым (в UTF-8 или совместимом)."""
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(sample_size)
        if not chunk:
            return True
        # Пытаемся декодировать как UTF-8 — если не получается, но не крашит, считаем бинарным
        try:
            chunk.decode('utf-8')
            return True
        except UnicodeDecodeError:
            # Попытка с заменой: если хотя бы часть читается — считаем текстовым.
            # Это помогает избежать ложных срабатываний на бинарные данные
            try:
                chunk.decode('utf-8', errors='replace')
                return True
            except Exception:
                return False
    except Exception:
        return False

def collect_files(
    source_dir: str, 
    include_hidden: bool = False
) -> Dict[str, Dict[str, Any]]:
    """Собирает файлы из директории, включая/исключая скрытые по флагу."""
    result = {}
    source_path = Path(source_dir)

    for root, dirs, files in os.walk(source_dir):
        # Фильтрация скрытых папок (если не включены)
        if not include_hidden:
            dirs[:] = [d for d in dirs if not d.startswith('.')]

        for filename in files:
            # Пропускаем скрытые файлы, если не включены
            if not include_hidden and filename.startswith('.'):
                continue

            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, source_path)

            try:
                if is_text_file(full_path):
                    with open(full_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                else:
                    content = 'binaryfile'
            except Exception as e:
                print(f"⚠️  Warning: cannot read '{rel_path}': {e}", file=sys.stderr)
                content = 'binaryfile'

            result[rel_path] = {"content": content}

    return result

def print_file_structure(data: Dict[str, Dict[str, Any]], mode: str, source: str, dest: str) -> None:
    """Выводит структуру файлов, участвующих в операции."""
    print(f"\n📁 Операция: {mode}")
    print(f"📂 Источник: {source}")
    print(f"📂 Назначение: {dest}")
    print("\n📋 Структура файлов:")
    
    # Группируем файлы по директориям для лучшей читаемости
    dirs = {}
    for rel_path, file_data in data.items():
        path_parts = rel_path.split(os.sep)
        if len(path_parts) > 1:
            dir_name = path_parts[0]
            if dir_name not in dirs:
                dirs[dir_name] = []
            dirs[dir_name].append((rel_path, file_data))
        else:
            if '' not in dirs:
                dirs[''] = []
            dirs[''].append((rel_path, file_data))
    
    # Сначала выводим файлы в корне
    if '' in dirs:
        for rel_path, file_data in sorted(dirs['']):
            file_type = "📄" if file_data.get("content") != "binaryfile" else "🔷"
            print(f"  {file_type} {rel_path}")
        del dirs['']
    
    # Затем по директориям
    for dir_name in sorted(dirs.keys()):
        print(f"  📁 {dir_name}/")
        for rel_path, file_data in sorted(dirs[dir_name]):
            file_name = os.path.basename(rel_path)
            file_type = "📄" if file_data.get("content") != "binaryfile" else "🔷"
            print(f"    {file_type} {file_name}")
    
    # Статистика
    text_files = sum(1 for f in data.values() if f.get("content") != "binaryfile")
    binary_files = len(data) - text_files
    print(f"\n📊 Статистика:")
    print(f"   Текстовых файлов: {text_files}")
    print(f"   Бинарных файлов: {binary_files}")
    print(f"   Всего файлов: {len(data)}")

def extract_from_json(
    json_path: str,
    target_dir: str,
    overwrite_all: bool = False,
    include_hidden: bool = False,
    skip_confirmation: bool = False
) -> None:
    """Разворачивает JSON обратно в файлы."""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"❌ Ошибка чтения JSON: {e}", file=sys.stderr)
        return

    # Показываем структуру перед восстановлением
    print_file_structure(data, "EXTRACT", json_path, target_dir)
    
    # Запрашиваем подтверждение, если не установлен флаг --yes
    if not skip_confirmation:
        response = input("\n❓ Продолжить восстановление? [y/N] ").strip().lower()
        if response not in ('y', 'yes'):
            print("⏹️  Операция отменена пользователем.")
            return
        print()

    for rel_path, file_data in data.items():
        # Пропускаем, если это бинарный файл (не восстанавливаем)
        if file_data.get("content") == "binaryfile":
            print(f"⏭️  Пропуск бинарного файла: {rel_path}")
            continue

        target_path = Path(target_dir) / rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Проверка существования
        if target_path.exists():
            if not overwrite_all:
                resp = input(f"Перезаписать '{rel_path}'? [y/N] ").strip().lower()
                if resp not in ('y', 'yes'):
                    print(f"❌ Пропущен: {rel_path}")
                    continue
            else:
                print(f"🔄 Перезапись (авто): {rel_path}")

        # Запись текстового содержимого
        content = file_data.get("content", "")
        try:
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"✅ Восстановлен: {rel_path}")
        except Exception as e:
            print(f"❌ Ошибка записи '{rel_path}': {e}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(
        description="Сохранить содержимое директории в JSON или восстановить из JSON (только текстовые файлы)."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True, help="Режим работы")

    # --- Пакет для архивирования (JSON из папки) ---
    archive_parser = subparsers.add_parser("archive", help="Создать JSON из директории")
    archive_parser.add_argument("source_dir", help="Исходная директория")
    archive_parser.add_argument("output_json", help="Выходной JSON-файл")
    archive_parser.add_argument(
        "--hidden", "-H", action="store_true", 
        help="Включить скрытые файлы (начинающиеся с .)"
    )

    # --- Пакет для восстановления (JSON в папку) ---
    extract_parser = subparsers.add_parser("extract", help="Восстановить файлы из JSON")
    extract_parser.add_argument("json_file", help="Входной JSON-файл")
    extract_parser.add_argument("target_dir", help="Целевая директория")
    extract_parser.add_argument(
        "--hidden", "-H", action="store_true", 
        help="Восстанавливать в скрытые имена (не фильтровать)"
    )
    extract_parser.add_argument(
        "--yes", "-y", action="store_true", 
        help="Автоматически перезаписывать существующие файлы и пропустить подтверждение (без запроса)"
    )


    args = parser.parse_args()

    if args.mode == "archive":
        if not os.path.isdir(args.source_dir):
            print(f"❌ Ошибка: '{args.source_dir}' не является директорией", file=sys.stderr)
            sys.exit(1)

        # Собираем данные для предварительного просмотра
        data = collect_files(args.source_dir, include_hidden=args.hidden)
        
        # Показываем структуру перед созданием JSON
        print_file_structure(data, "ARCHIVE", args.source_dir, args.output_json)
        
        # Запрашиваем подтверждение
        if not args.yes:
            response = input("\n❓ Продолжить создание JSON? [y/N] ").strip().lower()
            if response not in ('y', 'yes'):
                print("⏹️  Операция отменена пользователем.")
                sys.exit(0)
        
        with open(args.output_json, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\n✅ JSON-архив создан: {args.output_json}")
        print(f"📁 Записано файлов: {len(data)}")

    elif args.mode == "extract":
        if not os.path.isfile(args.json_file):
            print(f"❌ Ошибка: '{args.json_file}' не найден", file=sys.stderr)
            sys.exit(1)
        if not os.path.isdir(args.target_dir):
            os.makedirs(args.target_dir, exist_ok=True)
            print(f"📁 Создана целевая директория: {args.target_dir}")

        extract_from_json(
            args.json_file,
            args.target_dir,
            overwrite_all=args.yes,
            include_hidden=args.hidden,
            skip_confirmation=args.yes
        )

if __name__ == "__main__":
    main()

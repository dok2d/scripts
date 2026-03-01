#!/usr/bin/env python3
"""
Конвертер выгрузки Telegram-чата в текстовый формат для подготовки саммари.
Использование: python tg_to_summary.py chat.json [-o output.txt] [--from DATE] [--to DATE] [--no-reactions] [--no-replies]
"""

import json
import argparse
from datetime import datetime
from pathlib import Path


def parse_text(text_field) -> str:
    """Только поле text — строка или список entities. text_entities игнорируем (дубль)."""
    if isinstance(text_field, str):
        return text_field.strip()
    if isinstance(text_field, list):
        return "".join(
            part["text"] if isinstance(part, dict) else part
            for part in text_field
        ).strip()
    return ""


def format_reactions(reactions: list) -> str:
    if not reactions:
        return ""
    parts = [f"{r['emoji']}×{r['count']}" for r in reactions if r.get("emoji")]
    return f" [{' '.join(parts)}]" if parts else ""


def format_reply(msg: dict, messages_by_id: dict, id_map: dict) -> str:
    reply_id = msg.get("reply_to_message_id")
    if not reply_id or reply_id not in messages_by_id:
        return ""
    orig = messages_by_id[reply_id]
    orig_text = parse_text(orig.get("text", ""))
    if not orig_text:
        return ""
    short = orig_text[:60] + ("…" if len(orig_text) > 60 else "")
    orig_uid = orig.get("from_id", "")
    orig_label = id_map.get(orig_uid, orig.get("from", "?"))
    return f" ↩{orig_label}:«{short}»"


def build_sender_index(messages: list):
    """
    Возвращает:
      id_map:   {user_id -> короткий ярлык}  (U01, U02, …)
      name_map: {user_id -> полное имя}
    Сортируем по количеству сообщений (самый активный — U01).
    """
    counts = {}
    name_map = {}
    for m in messages:
        if m.get("type") != "message":
            continue
        uid = m.get("from_id", "")
        name = m.get("from", "?")
        if uid:
            counts[uid] = counts.get(uid, 0) + 1
            name_map[uid] = name

    sorted_uids = sorted(counts, key=lambda u: counts[u], reverse=True)
    id_map = {uid: f"U{str(i+1).zfill(2)}" for i, uid in enumerate(sorted_uids)}
    return id_map, name_map


def convert(input_path: str, output_path: str,
            date_from: str = None, date_to: str = None,
            no_reactions: bool = False, no_replies: bool = False):

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    chat_name = data.get("name", "Чат")
    raw_messages = data.get("messages", [])

    # Дедупликация по ID
    seen_ids = set()
    messages = []
    for m in raw_messages:
        mid = m.get("id")
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        messages.append(m)

    dt_from = datetime.fromisoformat(date_from) if date_from else None
    dt_to   = datetime.fromisoformat(date_to)   if date_to   else None

    id_map, name_map = build_sender_index(messages)

    messages_by_id = {} if no_replies else {
        m["id"]: m for m in messages if m.get("type") == "message"
    }

    lines = []

    # Шапка: участники
    lines.append(f"# {chat_name}")
    lines.append("")
    lines.append("## Участники")
    for uid, label in id_map.items():
        lines.append(f"{label} — {name_map[uid]}")
    lines.append("")
    lines.append("## Сообщения")

    current_day = None
    msg_count = 0

    for msg in messages:
        mtype = msg.get("type")

        if mtype != "message":
            continue

        dt = datetime.fromisoformat(msg["date"])
        if dt_from and dt < dt_from:
            continue
        if dt_to and dt > dt_to:
            continue

        # Только текст — вложения без подписи пропускаем целиком
        text = parse_text(msg.get("text", ""))
        if not text:
            continue

        uid = msg.get("from_id", "")
        author = id_map.get(uid, msg.get("from", "?"))
        time_str = dt.strftime("%H:%M")

        reactions = "" if no_reactions else format_reactions(msg.get("reactions", []))
        reply     = "" if no_replies   else format_reply(msg, messages_by_id, id_map)

        day = dt.date()
        if day != current_day:
            current_day = day
            lines.append(f"\n── {dt.strftime('%d.%m.%Y')} ──")

        lines.append(f"[{time_str}] {author}:{reply} {text}{reactions}")
        msg_count += 1

    lines.append(f"\n# Итого: {msg_count} сообщений")

    result = "\n".join(lines)

    if output_path:
        Path(output_path).write_text(result, encoding="utf-8")
        kb_in  = Path(input_path).stat().st_size // 1024
        kb_out = Path(output_path).stat().st_size // 1024
        print(f"✓ {output_path}  |  {msg_count} сообщений  |  {kb_in} KB → {kb_out} KB")
    else:
        print(result)


def main():
    parser = argparse.ArgumentParser(description="Конвертер Telegram-экспорта для саммари")
    parser.add_argument("input",           help="Путь к result.json")
    parser.add_argument("-o", "--output",  help="Выходной файл (по умолчанию stdout)")
    parser.add_argument("--from",  dest="date_from", help="Начало периода YYYY-MM-DD")
    parser.add_argument("--to",    dest="date_to",   help="Конец периода YYYY-MM-DD")
    parser.add_argument("--no-reactions", action="store_true", help="Не включать реакции")
    parser.add_argument("--no-replies",   action="store_true", help="Не включать цитаты реплаев")
    args = parser.parse_args()

    convert(args.input, args.output, args.date_from, args.date_to,
            args.no_reactions, args.no_replies)


if __name__ == "__main__":
    main()

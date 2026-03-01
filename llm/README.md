# Утилиты для работы с LLM (Large Language Models)

Директория содержит инструменты для подготовки данных к использованию с большими языковыми моделями.

## Содержимое

### `dir2prompt.py`

**Назначение:** Конвертация директории с файлами в JSON-формат и обратно для удобной передачи в контекст LLM.

**Описание:**
Скрипт позволяет "упаковать" содержимое директории (только текстовые файлы, кодировка UTF-8) в единый JSON-файл, который можно отправить в запросе к LLM. Также поддерживается обратная операция — восстановление файлов из JSON.

**Возможности:**
- Автоматическое определение текстовых файлов (бинарные помечаются как `binaryfile`)
- Игнорирование скрытых файлов/папок (можно включить флагом)
- При восстановлении — проверка на перезапись с запросом подтверждения

**Режимы работы:**

**archive** — создание JSON из директории:
```bash
./dir2prompt.py archive /path/to/dir output.json
./dir2prompt.py archive /path/to/dir output.json --hidden  # включить скрытые файлы
```

**extract** — восстановление файлов из JSON:
```bash
./dir2prompt.py extract output.json /path/to/restore
./dir2prompt.py extract output.json /path/to/restore --yes   # авто-перезапись
./dir2prompt.py extract output.json /path/to/restore --hidden  # восстанавливать скрытые
```

**Пример JSON на выходе:**
```json
{
  "script.sh": {
    "content": "#!/bin/bash\necho 'Hello'"
  },
  "image.png": {
    "content": "binaryfile"
  }
}
```
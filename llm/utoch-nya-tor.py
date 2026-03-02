#!/usr/bin/env python3
"""
Самопроверка ответов LLM через llama.cpp API с поддержкой русского языка.
Быстрая версия - без таймаутов, только реакция на ошибки сервиса.

Пример запуска:
    python self_check_llama.py --llama-host localhost --llama-port 11434 --server-port 8001 -v
"""

import json
import re
import sys
import argparse
import http.server
import socketserver
import urllib.request
import urllib.error
import threading
import time

# Настройка кодировки для Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')

# ANSI-коды для цветов
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"

def color(text: str, color_code: str) -> str:
    return f"{color_code}{text}{Colors.RESET}"

# --------------------------
# Конфигурация по умолчанию
# --------------------------
DEFAULT_LLAMA_HOST = "localhost"
DEFAULT_LLAMA_PORT = 11434
DEFAULT_SERVER_PORT = 8001
DEFAULT_VERBOSITY = 0
MAX_RETRY_ATTEMPTS = 10
RETRY_DELAY = 1  # Задержка только между попытками при ошибках

SELF_CHECK_PROMPT_TEMPLATE = """Ты — эксперт по критической оценке ответов. Ниже приведён исходный вопрос и ответ на него.

ВОПРОС:
{question}

ОТВЕТ:
{answer}

Оцени ответ по шкале от 0 до 10:
0 — полностью не относится к вопросу, неверен или пустой
10 — полностью, точно и полно отвечает на вопрос, без ошибок и недостатков

Отвечай ТОЛЬКО числом от 0 до 10. Не добавляй пояснений."""

IMPROVE_PROMPT_TEMPLATE = """Переформулируй и улучши ответ, учитывая критику и требования к точности.

ВОПРОС:
{question}

ПРЕДЫДУЩИЙ ОТВЕТ:
{answer}

КРИТИКА (необходимо улучшить):
{rating_reason}

Новый ответ (только текст, без вступлений):
"""

# Глобальная переменная для уровня verbosity
VERBOSE_LEVEL = 0

def log(msg: str, level=1, color_code=Colors.BLUE):
    if VERBOSE_LEVEL >= level:
        prefix = f"[{'INFO' if level == 1 else 'DEBUG' if level == 2 else 'TRACE'}]"
        print(color(f"{prefix} {msg}", color_code), flush=True)

def log_warn(msg: str):
    print(color(f"[WARN] {msg}", Colors.YELLOW), file=sys.stderr, flush=True)

def log_error(msg: str):
    print(color(f"[ERR] {msg}", Colors.RED), file=sys.stderr, flush=True)

# --------------------------
# Вспомогательные функции
# --------------------------

def safe_decode_payload(payload: bytes, max_len: int = 200) -> str:
    """Безопасно декодирует байтовый payload для логирования."""
    try:
        decoded = payload.decode('utf-8')
        if len(decoded) > max_len:
            return decoded[:max_len] + "..."
        return decoded
    except UnicodeDecodeError:
        return repr(payload[:max_len]) + "..."

def call_llama_api(host: str, port: int, prompt: str, max_tokens: int = 512, debug_label: str = "LLM") -> str:
    """
    Отправляет запрос в llama.cpp API и возвращает текст ответа.
    Без таймаутов - ждём сколько нужно, реагируем только на ошибки сервиса.
    """
    url = f"http://{host}:{port}/completion"
    payload_data = {
        "prompt": prompt,
        "n_predict": max_tokens,
        "stream": False
    }
    payload = json.dumps(payload_data, ensure_ascii=False).encode('utf-8')

    if VERBOSE_LEVEL >= 3:
        log(f"🚀 {debug_label} → {url}", level=3, color_code=Colors.BLUE)
        log(f"Payload: {safe_decode_payload(payload)}", level=3, color_code=Colors.BLUE)

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            # Убрали timeout - ждём сколько нужно
            with urllib.request.urlopen(req) as resp:
                response_data = json.loads(resp.read().decode('utf-8'))
                if VERBOSE_LEVEL >= 3:
                    content = response_data.get('content', '')
                    log(f"✅ {debug_label} ← {len(content)} символов", level=3, color_code=Colors.GREEN)
                return response_data.get("content", "").strip()
        except urllib.error.HTTPError as e:
            # Реагируем только на HTTP ошибки (проблемы сервиса)
            log_error(f"HTTP Error {e.code}: {e.reason} (URL: {url})")
            if attempt < MAX_RETRY_ATTEMPTS:
                log(f"Повторная попытка {attempt}/{MAX_RETRY_ATTEMPTS} через {RETRY_DELAY}с...", level=1)
                time.sleep(RETRY_DELAY)
            else:
                return ""
        except urllib.error.URLError as e:
            # Ошибки соединения (сервер недоступен)
            log_error(f"Connection Error: {e.reason}")
            if attempt < MAX_RETRY_ATTEMPTS:
                log(f"Повторная попытка {attempt}/{MAX_RETRY_ATTEMPTS} через {RETRY_DELAY}с...", level=1)
                time.sleep(RETRY_DELAY)
            else:
                return ""
        except Exception as e:
            # Другие неожиданные ошибки
            log_error(f"Неожиданная ошибка: {e}")
            if attempt < MAX_RETRY_ATTEMPTS:
                log(f"Повторная попытка {attempt}/{MAX_RETRY_ATTEMPTS} через {RETRY_DELAY}с...", level=1)
                time.sleep(RETRY_DELAY)
            else:
                return ""
    return ""

def parse_rating(text: str) -> float:
    """Извлекает число от 0 до 10 из строки."""
    if not text:
        return 0.0
    match = re.search(r'\b([0-9](?:\.[0-9])?|10(?:\.0+)?)\b', text)
    if match:
        val = float(match.group(1))
        return min(max(val, 0.0), 10.0)
    return 0.0

def self_check(question: str, answer: str, llama_host: str, llama_port: int, attempt: int = 1) -> tuple[float, str]:
    """Возвращает (оценка, пояснение) для ответа."""
    if not answer:
        return 0.0, "Пустой ответ"
        
    prompt = SELF_CHECK_PROMPT_TEMPLATE.format(question=question, answer=answer)
    log(f"🔍 Самопроверка (попытка {attempt})", level=1, color_code=Colors.BLUE)
    if VERBOSE_LEVEL >= 2:
        log(f"Промпт самопроверки:\n{prompt[:300]}{'...' if len(prompt) > 300 else ''}", level=2, color_code=Colors.BLUE)

    rating_text = call_llama_api(llama_host, llama_port, prompt, max_tokens=8, debug_label="SelfCheck")
    rating = parse_rating(rating_text)
    log(f"📊 Оценка: {rating:.1f}/10", level=1, color_code=Colors.GREEN if rating >= 7 else Colors.YELLOW)

    if VERBOSE_LEVEL >= 2:
        log(f"Пояснение оценки: {rating_text or '(не распознано)'}", level=2, color_code=Colors.BLUE)
    return rating, rating_text or "Оценка не распознана"

def improve_answer(question: str, answer: str, rating: float, rating_reason: str,
                   llama_host: str, llama_port: int) -> str:
    """Генерирует улучшенный ответ."""
    reason = rating_reason if rating_reason and rating_reason != "Оценка не распознана" else f"Оценка: {rating:.1f}/10"
    prompt = IMPROVE_PROMPT_TEMPLATE.format(
        question=question,
        answer=answer,
        rating_reason=reason
    )
    log(f"🔄 Генерация улучшенного ответа", level=1, color_code=Colors.BLUE)
    if VERBOSE_LEVEL >= 2:
        log(f"Промпт улучшения (первые 200 символов):\n{prompt[:200]}...", level=2, color_code=Colors.BLUE)

    improved = call_llama_api(llama_host, llama_port, prompt, max_tokens=512, debug_label="Improve")
    if VERBOSE_LEVEL >= 2 and improved:
        log(f"Улучшенный ответ:\n{improved[:300]}{'...' if len(improved) > 300 else ''}", level=2, color_code=Colors.GREEN)
    return improved

def generate_with_self_check(prompt: str,
                             llama_host: str, llama_port: int,
                             max_attempts: int = 3,
                             threshold: float = 7.0,
                             max_retries: int = MAX_RETRY_ATTEMPTS) -> tuple[str, float]:
    """
    Запрашивает ответ, проверяет, улучшает при необходимости.
    Без таймаутов - только реакция на ошибки сервиса.
    """
    # Получаем базовый ответ (без таймаута, ждём сколько нужно)
    log(f"💬 Запрос к LLM", level=1, color_code=Colors.BLUE)
    if VERBOSE_LEVEL >= 2:
        log(f"Исходный промпт:\n{prompt[:200]}...", level=2, color_code=Colors.BLUE)

    answer = call_llama_api(llama_host, llama_port, prompt, max_tokens=512, debug_label="Base")
    
    if not answer:
        log_error("Не удалось получить ответ от LLM")
        return "", 0.0

    log(f"✅ Получен ответ (длина: {len(answer)} символов)", level=1, color_code=Colors.GREEN)

    # Оценка ответа
    rating, rating_reason = self_check(prompt, answer, llama_host, llama_port, attempt=1)

    if rating >= threshold:
        log(f"✅ Ответ принят (оценка {rating:.1f} ≥ {threshold})", level=1, color_code=Colors.GREEN)
        return answer, rating

    log_warn(f"Ответ низкокачественный (оценка {rating:.1f} < {threshold}) — начинаю улучшение")

    # Итеративное улучшение
    for attempt in range(1, max_attempts):
        log(f"🔄 Улучшение (попытка {attempt + 1}/{max_attempts})", level=1, color_code=Colors.BLUE)
        answer = improve_answer(prompt, answer, rating, rating_reason, llama_host, llama_port)
        if not answer:
            log_error("Улучшенный ответ пустой — прерываюсь.")
            break

        rating, rating_reason = self_check(prompt, answer, llama_host, llama_port, attempt=attempt + 1)
        if rating >= threshold:
            log(f"✅ Улучшенный ответ принят (оценка {rating:.1f})", level=1, color_code=Colors.GREEN)
            break
    else:
        log_warn(f"После {max_attempts} попыток оценка всё ещё ниже порога ({rating:.1f})")

    return answer, rating

# --------------------------
# HTTP-сервер
# --------------------------

class LlamaLikeHandler(http.server.BaseHTTPRequestHandler):
    llama_host: str = None
    llama_port: int = None

    def log_message(self, format, *args):
        # Отключаем стандартный лог
        pass

    def do_POST(self):
        if self.path == "/completion":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            
            try:
                # Пробуем декодировать как UTF-8
                try:
                    body_str = body.decode('utf-8')
                except UnicodeDecodeError:
                    # Fallback на другие кодировки
                    body_str = body.decode('utf-8', errors='replace')
                    log_warn("Использована замена символов (replace) для декодирования")

                data = json.loads(body_str)
                prompt = data.get("prompt", "")

                # Генерируем ответ без таймаутов
                answer, rating = generate_with_self_check(
                    prompt,
                    self.llama_host,
                    self.llama_port,
                    max_attempts=3,
                    threshold=7.0
                )

                response = {
                    "content": answer,
                    "rating": rating,
                    "status": "success" if answer else "error"
                }
                
                response_json = json.dumps(response, ensure_ascii=False)
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(response_json.encode('utf-8'))
                return

            except json.JSONDecodeError as e:
                log_error(f"Ошибка парсинга JSON: {e}")
                self.send_error(400, f"Invalid JSON: {e}")
            except Exception as e:
                log_error(f"Внутренняя ошибка сервера: {e}")
                self.send_error(500, f"Internal server error: {e}")
        else:
            self.send_error(404, "Not found")

# --------------------------
# main
# --------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Самопроверка LLM через llama.cpp API (быстрая версия)",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--llama-host", type=str, default=DEFAULT_LLAMA_HOST,
                        help=f"Хост llama.cpp API (по умолчанию: {DEFAULT_LLAMA_HOST})")
    parser.add_argument("--llama-port", type=int, default=DEFAULT_LLAMA_PORT,
                        help=f"Порт llama.cpp API (по умолчанию: {DEFAULT_LLAMA_PORT})")
    parser.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT,
                        help=f"Порт сервера (по умолчанию: {DEFAULT_SERVER_PORT})")
    parser.add_argument("--max-retries", type=int, default=MAX_RETRY_ATTEMPTS,
                        help=f"Попытки при ошибках сервиса (по умолчанию: {MAX_RETRY_ATTEMPTS})")
    parser.add_argument("--retry-delay", type=int, default=RETRY_DELAY,
                        help=f"Задержка между попытками при ошибках (по умолчанию: {RETRY_DELAY})")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="Уровень подробности: -v (info), -vv (debug), -vvv (trace)")
    return parser.parse_args()

def run_server(host: str, port: int, llama_host: str, llama_port: int, verbosity: int, max_retries: int, retry_delay: int):
    global VERBOSE_LEVEL, MAX_RETRY_ATTEMPTS, RETRY_DELAY
    VERBOSE_LEVEL = verbosity
    MAX_RETRY_ATTEMPTS = max_retries
    RETRY_DELAY = retry_delay

    LlamaLikeHandler.llama_host = llama_host
    LlamaLikeHandler.llama_port = llama_port

    # Быстрая проверка подключения
    test_prompt = "Привет"
    log("🧪 Проверка подключения к llama.cpp...", level=1, color_code=Colors.BLUE)
    try:
        test_resp = call_llama_api(llama_host, llama_port, test_prompt, max_tokens=8)
        if test_resp:
            log(f"✅ Подключено. Пример ответа: «{test_resp[:50]}...»", level=1, color_code=Colors.GREEN)
        else:
            log_error(f"Не удалось получить ответ от llama.cpp")
            sys.exit(1)
    except Exception as e:
        log_error(f"Ошибка подключения: {e}")
        sys.exit(1)

    with socketserver.TCPServer((host, port), LlamaLikeHandler) as httpd:
        print(f"\n{color('✅ Сервер запущен', Colors.GREEN)} на {color(f'http://{host}:{port}', Colors.BOLD)}")
        print(f"🔗 Подключено к llama.cpp API: {llama_host}:{llama_port}")
        print(f"📊 Уровень подробности: {verbosity} {'v' * verbosity}")
        print(f"🔄 Попытки при ошибках: {max_retries} с задержкой {retry_delay}с\n")
        print("📡 Ожидание запросов (Ctrl+C для остановки)...\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\n{color('👋 Сервер остановлен', Colors.BLUE)}")

if __name__ == "__main__":
    args = parse_args()
    run_server("0.0.0.0", args.server_port, args.llama_host, args.llama_port, 
               args.verbose, args.max_retries, args.retry_delay)

#!/usr/bin/env python3
"""
Prettify minified web files (JSON, HTML, JS, CSS).
Auto-detects format and outputs human-readable text.

Usage:
    prettify.py <file>
    prettify.py -              # read from stdin
    prettify.py <file> -o out  # write to file
"""

import argparse
import json
import re
import sys


def detect_format(content):
    """Auto-detect content format."""
    stripped = content.strip()

    # JSON: starts with { or [
    if stripped and stripped[0] in ('{', '['):
        try:
            json.loads(stripped)
            return 'json'
        except json.JSONDecodeError:
            pass

    # HTML: starts with < or has doctype
    if re.match(r'(?i)^\s*(<!doctype|<html|<head|<body|<div|<span|<p\b|<a\b|<script|<style|<link|<meta|<table|<form|<ul|<ol|<h[1-6])', stripped):
        return 'html'
    if stripped.startswith('<') and '>' in stripped:
        return 'html'

    # CSS: contains selectors with braces (but not JS-like constructs)
    if re.search(r'[.#@][a-zA-Z][\w-]*\s*\{', stripped) or re.search(r'(?:^|\})\s*[a-z][\w-]*\s*\{[^()]*\}', stripped):
        return 'css'
    if re.search(r'@(media|keyframes|font-face|import|charset)\b', stripped):
        return 'css'

    # JS: fallback for anything with function/var/let/const/=>/class patterns
    if re.search(r'\b(function|var|let|const|class|import|export|require|module\.exports|=>)\b', stripped):
        return 'js'

    return None


def prettify_json(content):
    data = json.loads(content)
    return json.dumps(data, indent=2, ensure_ascii=False)


def prettify_html(content):
    """Simple HTML indenter without external dependencies."""
    result = []
    indent = 0
    indent_str = '  '

    # Void elements that don't have closing tags
    void_tags = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    # Split into tags and text
    tokens = re.split(r'(<[^>]+>)', content)

    for token in tokens:
        token = token.strip()
        if not token:
            continue

        if token.startswith('</'):
            indent = max(indent - 1, 0)
            result.append(indent_str * indent + token)
        elif token.startswith('<'):
            result.append(indent_str * indent + token)
            tag_match = re.match(r'<\s*([a-zA-Z][a-zA-Z0-9]*)', token)
            if tag_match:
                tag_name = tag_match.group(1).lower()
                is_self_closing = token.endswith('/>')
                if tag_name not in void_tags and not is_self_closing:
                    indent += 1
        else:
            result.append(indent_str * indent + token)

    return '\n'.join(result)


def prettify_css(content):
    """Format minified CSS."""
    out = content

    # Newline after { and indent
    out = re.sub(r'\{', ' {\n', out)
    out = re.sub(r'\}', '\n}\n\n', out)
    out = re.sub(r';\s*', ';\n', out)

    # Indent properties inside blocks
    lines = out.split('\n')
    result = []
    indent = 0
    for line in lines:
        line = line.strip()
        if not line:
            if result and result[-1] != '':
                result.append('')
            continue
        if line == '}':
            indent = max(indent - 1, 0)
            result.append('  ' * indent + line)
        elif line.endswith('{'):
            result.append('  ' * indent + line)
            indent += 1
        else:
            result.append('  ' * indent + line)

    return '\n'.join(result).strip() + '\n'


def prettify_js(content):
    """Basic JS formatter — handles braces, semicolons, and indentation."""
    result = []
    indent = 0
    indent_str = '  '
    i = 0
    line = ''
    in_string = None
    escape = False

    while i < len(content):
        ch = content[i]

        # Handle string literals
        if escape:
            line += ch
            escape = False
            i += 1
            continue

        if ch == '\\' and in_string:
            line += ch
            escape = True
            i += 1
            continue

        if ch in ('"', "'", '`'):
            if in_string is None:
                in_string = ch
            elif in_string == ch:
                in_string = None
            line += ch
            i += 1
            continue

        if in_string:
            line += ch
            i += 1
            continue

        # Handle single-line comments
        if ch == '/' and i + 1 < len(content) and content[i + 1] == '/':
            end = content.find('\n', i)
            if end == -1:
                line += content[i:]
                i = len(content)
            else:
                line += content[i:end]
                result.append(indent_str * indent + line.strip())
                line = ''
                i = end + 1
            continue

        # Handle multi-line comments
        if ch == '/' and i + 1 < len(content) and content[i + 1] == '*':
            end = content.find('*/', i + 2)
            if end == -1:
                line += content[i:]
                i = len(content)
            else:
                line += content[i:end + 2]
                i = end + 2
            continue

        if ch == '{':
            line += ' {'
            result.append(indent_str * indent + line.strip())
            line = ''
            indent += 1
            i += 1
            continue

        if ch == '}':
            if line.strip():
                result.append(indent_str * indent + line.strip())
                line = ''
            indent = max(indent - 1, 0)
            result.append(indent_str * indent + '}')
            i += 1
            continue

        if ch == ';':
            line += ';'
            result.append(indent_str * indent + line.strip())
            line = ''
            i += 1
            continue

        if ch == '\n':
            if line.strip():
                result.append(indent_str * indent + line.strip())
                line = ''
            i += 1
            continue

        line += ch
        i += 1

    if line.strip():
        result.append(indent_str * indent + line.strip())

    return '\n'.join(result)


def main():
    parser = argparse.ArgumentParser(description='Prettify minified JSON/HTML/JS/CSS')
    parser.add_argument('file', help='Input file (use - for stdin)')
    parser.add_argument('-o', '--output', help='Output file (default: stdout)')
    parser.add_argument('-f', '--format', choices=['json', 'html', 'js', 'css'],
                        help='Force format instead of auto-detection')
    args = parser.parse_args()

    if args.file == '-':
        content = sys.stdin.read()
    else:
        with open(args.file, 'r', encoding='utf-8') as f:
            content = f.read()

    fmt = args.format or detect_format(content)
    if fmt is None:
        print('Error: could not detect format. Use -f to specify.', file=sys.stderr)
        sys.exit(1)

    formatters = {
        'json': prettify_json,
        'html': prettify_html,
        'css': prettify_css,
        'js': prettify_js,
    }

    print(f'Detected format: {fmt}', file=sys.stderr)
    result = formatters[fmt](content)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(result)
        print(f'Written to {args.output}', file=sys.stderr)
    else:
        print(result)


if __name__ == '__main__':
    main()

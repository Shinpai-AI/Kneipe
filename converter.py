#!/usr/bin/env python3
"""Kneipen-Schlägerei — MD → JSON Converter
Liest Themen-MDs und generiert spielbare JSONs.
Shinpai Games | Ist einfach passiert."""

import json, os, re, sys

THEMEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Themen')
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'themen_json')

# --- FLAGS ---
FLAG_RE = re.compile(r'\[(JA-SAGER|MAUERBLÜMCHEN|JUKEBOX|STAMMGAST)\]')

def parse_flags(text):
    """Extrahiere Trigger-Flags aus Antwort-Text"""
    flags = FLAG_RE.findall(text)
    clean = FLAG_RE.sub('', text).strip()
    return clean, [f.lower().replace('ü', 'ue') for f in flags]

def parse_answer(line):
    """Parse eine Antwort-Zeile: '- A: "Text" → Schicht 2A [FLAGS]'"""
    m = re.match(r'^-\s+([ABC]):\s+(.+?)(?:\s*→\s*(.+?))?$', line.strip())
    if not m:
        return None
    choice = m.group(1)
    raw_text = m.group(2).strip()
    target = m.group(3).strip() if m.group(3) else None

    # Flags extrahieren
    text, flags = parse_flags(raw_text)

    # Sternchen für Schweigen entfernen
    if text.startswith('*') and text.endswith('*'):
        text = text[1:-1].strip()
        is_silence = True
    else:
        is_silence = False

    return {
        'choice': choice,
        'text': text,
        'target': target,
        'flags': flags,
        'silence': is_silence,
    }

def parse_theme_md(filepath):
    """Parse eine komplette Themen-MD Datei"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    theme = {
        'id': os.path.splitext(os.path.basename(filepath))[0],
        'title': '',
        'setting': '',
        'layers': {},
        'endings': {},
        'element_eval': '',
        'context_checks': [],
        'stammgast_capable': False,
    }

    # Titel — diverse Formate: "# Thema: X", "# Thema 21: X", "# X"
    title_m = re.search(r'^#\s+(?:Thema(?:\s+\d+)?:\s*)?(.+)$', content, re.MULTILINE)
    if title_m:
        theme['title'] = title_m.group(1).strip()

    # Setting — Format 1: "## Setting: TEXT" (gleiche Zeile)
    setting_m = re.search(r'^##\s+Setting:\s+(.+)$', content, re.MULTILINE)
    if setting_m:
        theme['setting'] = setting_m.group(1).strip()
    else:
        # Format 2: "## Setting\nTEXT" oder "## Setting\n\nTEXT" (nächste Zeile(n))
        setting_m2 = re.search(r'^##\s+Setting\s*\n+(.+?)(?=\n---|\n###|\n##)', content, re.MULTILINE | re.DOTALL)
        if setting_m2:
            # Erste nicht-leere Zeile als Setting, Sonderzeilen ignorieren
            lines = [l.strip() for l in setting_m2.group(1).strip().split('\n') if l.strip() and not l.strip().startswith('**[')]
            theme['setting'] = ' '.join(lines) if lines else ''

    # Stammgast-fähig?
    if 'STAMMGAST' in content.upper() or 'stammgast' in content.lower():
        # Check ob Stammgast-Flags an C-Antworten hängen
        if '[STAMMGAST]' in content:
            theme['stammgast_capable'] = True

    # Schichten parsen
    sections = re.split(r'^###\s+', content, flags=re.MULTILINE)

    for section in sections:
        if not section.strip():
            continue

        lines = section.strip().split('\n')
        header = lines[0].strip()

        # Enden ZUERST prüfen: "Schicht 5-Feuer: 🔥" oder "5-Feuer" oder "5-Wasser" (mit oder ohne Doppelpunkt!)
        end_m = re.match(r'(?:Schicht\s+)?5-(\w+)(?::\s*(.*))?$', header)
        if end_m:
            # Das ist ein Ending, KEIN Layer!
            pass  # wird unten verarbeitet
        elif re.match(r'Schicht\s+5\b', header):
            # "Schicht 5: Endings" — Container, ignorieren
            pass
        else:
            # Schicht-Header: "Schicht 1: Der Auslöser" oder "Schicht 2A: Wofür?"
            layer_m = re.match(r'Schicht\s+(\S+):\s+(.+)', header)
            if layer_m:
                layer_id = layer_m.group(1).strip()
                layer_title = layer_m.group(2).strip()

                # Situationstext (> Zeilen)
                situation_lines = []
                answers = []

                for line in lines[1:]:
                    line = line.strip()
                    if line.startswith('>'):
                        situation_lines.append(line[1:].strip())
                    elif line.startswith('- '):
                        ans = parse_answer(line)
                        if ans:
                            answers.append(ans)

                # Schicht 4-Kern: Konvergenz-Layer mit Antworten wenn vorhanden
                theme['layers'][layer_id] = {
                    'id': layer_id,
                    'title': layer_title,
                    'situation': '\n'.join(situation_lines),
                    'answers': answers,
                }

        # Enden verarbeiten: "Schicht 5-Feuer: 🔥" oder "5-Feuer" (mit oder ohne Doppelpunkt!)
        end_m = re.match(r'(?:Schicht\s+)?5-(\w+)(?::\s*(.*))?$', header)
        if end_m:
            element = end_m.group(1).strip().lower()
            # Text sammeln (> Zeilen ODER direkt Text)
            end_text = []
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('>'):
                    end_text.append(line[1:].strip())
                elif not line.startswith('#') and not line.startswith('-'):
                    end_text.append(line)
            theme['endings'][element] = '\n'.join(end_text)

        # Element-Auswertung
        if header.startswith('Element-Auswertung'):
            eval_lines = [l.strip() for l in lines[1:] if l.strip().startswith('-')]
            theme['element_eval'] = '\n'.join(eval_lines)

        # Kontext-Schweigen-Check
        if header.startswith('Kontext-Schweigen-Check'):
            for line in lines[1:]:
                line = line.strip()
                if line.startswith('-'):
                    is_mauerblümchen = '⚠️' in line or 'Mauerblümchen' in line.lower() or 'mauerblümchen' in line.lower()
                    check_m = re.match(r'-\s+(.+?)=\s+(.+)', line)
                    if check_m:
                        theme['context_checks'].append({
                            'path': check_m.group(1).strip(),
                            'result': check_m.group(2).strip(),
                            'is_mauerblümchen': is_mauerblümchen,
                        })

    return theme

def convert_all():
    """Alle Themen konvertieren"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    themes = []
    md_files = sorted([f for f in os.listdir(THEMEN_DIR) if f.endswith('.md')])

    for md_file in md_files:
        filepath = os.path.join(THEMEN_DIR, md_file)
        print(f'  📖 {md_file}...', end=' ')
        try:
            theme = parse_theme_md(filepath)

            # JSON speichern
            json_name = theme['id'] + '.json'
            json_path = os.path.join(OUTPUT_DIR, json_name)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(theme, f, ensure_ascii=False, indent=2)

            themes.append({
                'id': theme['id'],
                'title': theme['title'],
                'setting': theme['setting'],
                'stammgast': theme['stammgast_capable'],
                'layers': len(theme['layers']),
            })
            print(f'✅ ({len(theme["layers"])} Schichten, {"🍺" if theme["stammgast_capable"] else ""})')
        except Exception as e:
            print(f'❌ Fehler: {e}')

    # Index speichern
    index_path = os.path.join(OUTPUT_DIR, '_index.json')
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump({'themes': themes, 'total': len(themes)}, f, ensure_ascii=False, indent=2)

    print(f'\n  🍺 {len(themes)} Themen konvertiert → {OUTPUT_DIR}/')
    return themes

if __name__ == '__main__':
    print('🍺 Kneipen-Schlägerei — MD → JSON Converter')
    print('=' * 50)
    convert_all()
    print('\n  🐉 Ist einfach passiert.')

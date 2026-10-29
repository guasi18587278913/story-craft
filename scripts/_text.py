"""Shared text handling. Standard library only; no source or draft is modified."""
import re
import unicodedata

def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text)
                   if not c.isspace() and unicodedata.category(c)[0] not in 'PZ')

def visible_count(text):
    return sum(unicodedata.category(c)[0] in 'LN' for c in text)

def sentences(text):
    """Split at sentence punctuation/newlines, keeping closing quotes and offsets."""
    result = []
    pattern = r'[^。！？!?；;\n]+(?:[。！？!?；;]+[”’」』"]*|(?=\n|$))'
    for match in re.finditer(pattern, text):
        raw = match.group().strip()
        if raw:
            start = match.start() + len(match.group()) - len(match.group().lstrip())
            result.append({'text': raw, 'start': start,
                           'line': text.count('\n', 0, start) + 1})
    return result

def context(text, start, end, radius=38):
    return text[max(0,start-radius):min(len(text),end+radius)].replace('\n',' ')

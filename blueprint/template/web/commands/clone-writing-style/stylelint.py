#!/usr/bin/env python3
"""Style lint + sentence-per-line reflow for LaTeX prose.

Hard rules (from Attila Karsai's QSRdissip.tex):
  R1  no semicolons
  R2  no em-dashes (---)
  R3  every prose sentence on its own line (reflow mode enforces)
  R4  inline math in prose preceded by "~" when it follows a word

Usage:
  stylelint.py check FILE      -- report violations only
  stylelint.py reflow FILE     -- rewrite prose to sentence-per-line (in place)
"""
import re
import sys

MATH_ENVS = ['equation', 'align', 'gather', 'multline', 'eqnarray']
BLOCK_ENVS = ['tabular', 'center', 'figure', 'table', 'tikzpicture',
              'pgfplots', 'subfigure', 'subtable']
ABBREVS = ['e.g', 'i.e', 'cf', 'w.r.t', 'et al', 'Fig', 'Sec', 'Thm', 'Eq',
           'Prop', 'Ex', 'resp', 'approx', 'vs', 'no', 'Ref', 'p', 'pp',
           'Vol', 'eds', 'ed', 'trans', 'Univ', 'Press', 'Proc', 'etc',
           'IEEE', 'SIAM', 'arXiv']

ABBREV_RE = re.compile(r'^(' + '|'.join(ABBREVS) + r')\.?$')
BOUND_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z\\$"\'(0-9]|\x00)')

STRUCTURAL = re.compile(
    r'^\\(?:begin|end|item|paragraph|section|subsection|subsubsection|'
    r'maketitle|title|author|date|keywords|ams|bibliographystyle|bibliography|'
    r'newtheorem|newcommand|renewcommand|DeclareMathOperator|label|caption|'
    r'input|includegraphics|noindent|thanks|footnotemark|footnote|url|hline|'
    r'toprule|midrule|bottomrule)\b'
    r'|^%|^\\+[^a-zA-Z]')


def is_structural(line):
    return bool(STRUCTURAL.search(line.strip()))


class Protector:
    def __init__(self):
        self.display = []
        self.inline = []

    def _ph(self, kind, text):
        store = self.display if kind == 'D' else self.inline
        idx = len(store)
        store.append(text.replace('\n', '\x01'))
        return f'\x00{kind}{idx}\x00'

    def protect_display(self, tex):
        pat = re.compile(
            r'\\begin\{(' + '|'.join(MATH_ENVS + BLOCK_ENVS) + r')\*?\}.*?\\end\{\1\*?\}',
            re.S)
        tex = pat.sub(lambda m: self._ph('D', m.group(0)), tex)
        tex = re.sub(r'\$\$.*?\$\$', lambda m: self._ph('D', m.group(0)), tex,
                     flags=re.S)
        tex = re.sub(r'\\\[.*?\\\]', lambda m: self._ph('D', m.group(0)), tex,
                     flags=re.S)
        return tex

    def protect_inline(self, tex):
        tex = re.sub(r'\$[^$]*\$', lambda m: self._ph('I', m.group(0)), tex)
        tex = re.sub(
            r'\\(cite|Cref|cref|eqref|ref|url|texttt)(\[[^]]*\])?\{[^}]*\}',
            lambda m: self._ph('I', m.group(0)), tex)
        return tex

    def restore(self, tex):
        for i, t in enumerate(self.display):
            tex = tex.replace(f'\x00D{i}\x00',
                              t.replace('\x01', '\n'))
        for i, t in enumerate(self.inline):
            tex = tex.replace(f'\x00I{i}\x00',
                              t.replace('\x01', '\n'))
        return tex


def _emit_sentence(s):
    """Emit one sentence, keeping display-math blocks on their own lines."""
    parts = re.split(r'(\x00D\d+\x00)', s)
    out = []
    cur = ''
    for p in parts:
        if re.fullmatch(r'\x00D\d+\x00', p):
            if cur:
                out.append(re.sub(r'\s+', ' ', cur).strip())
                cur = ''
            out.append(p)
        else:
            cur += p
    if cur:
        out.append(re.sub(r'\s+', ' ', cur).strip())
    return out


def reflow_paragraph(text):
    lines = [l for l in text.split('\n') if l.strip()]
    if not lines:
        return text
    buf = ''
    result = []
    n_boundaries = 0
    for line in lines:
        if is_structural(line):
            if buf:
                result.extend(_emit_sentence(buf))
                buf = ''
            result.append(line)
            continue
        buf = (buf + ' ' + line) if buf else line
        while True:
            m = BOUND_RE.search(buf)
            if not m:
                break
            pre = buf[:m.start()]
            tail = re.search(r'([A-Za-z.]{1,16})$', pre)
            if tail and ABBREV_RE.fullmatch(tail.group(1)):
                buf = buf[:m.start()] + '\x02' + buf[m.start() + 1:]
                continue
            result.extend(_emit_sentence(pre))
            buf = buf[m.end():]
            n_boundaries += 1
    if buf.strip():
        result.extend(_emit_sentence(buf))
    if n_boundaries == 0:
        # no sentence boundaries: keep the original line structure
        return text
    return '\n'.join(result) + ('\n' if text.endswith('\n') else '')


def split_sentences(tex):
    prot = Protector()
    tex = prot.protect_display(tex)
    tex = prot.protect_inline(tex)
    paras = re.split(r'(\n[ \t]*\n)', tex)
    out = []
    for chunk in paras:
        if re.fullmatch(r'\n[ \t]*\n', chunk):
            out.append('\n\n')
        else:
            out.append(reflow_paragraph(chunk))
    res = prot.restore(''.join(out))
    return res.replace('\x02', '.')


def extract_math(tex):
    pat = re.compile(r'\\begin\{(' + '|'.join(MATH_ENVS) + r')\*?\}.*?\\end\{\1\*?\}', re.S)
    return [m.group(0) for m in pat.finditer(tex)]


def lint(tex, src):
    issues = []
    for i, line in enumerate(tex.split('\n'), 1):
        s = line.strip()
        if not s or s.startswith('%'):
            continue
        if ';' in s:
            issues.append(f'{src}:{i}: semicolon: {s[:60]}')
        if '\u2014' in s or '---' in s:
            issues.append(f'{src}:{i}: em-dash: {s[:60]}')
    for m in re.finditer(r'\$[^$]*\$', tex):
        if m.group(0).startswith('$$'):
            continue
        pre = tex[max(0, m.start() - 60):m.start()]
        if re.search(r'[A-Za-z0-9]\s*$', pre) and '\\begin' not in pre[-30:]:
            ln = tex[:m.start()].count('\n') + 1
            issues.append(f'{src}:{ln}: inline math without ~: ...{pre[-40:]}$...')
    return issues


def main():
    mode, path = sys.argv[1], sys.argv[2]
    tex = open(path).read()
    if mode == 'check':
        issues = lint(tex, path)
        for it in issues:
            print(it)
        print(f'{len(issues)} style issues')
    elif mode == 'reflow':
        before = extract_math(tex)
        tex2 = split_sentences(tex)
        after = extract_math(tex2)
        if before != after:
            print('ERROR: math environments changed by reflow!')
            sys.exit(1)
        open(path, 'w').write(tex2)
        print('reflowed; math envs unchanged')


if __name__ == '__main__':
    main()

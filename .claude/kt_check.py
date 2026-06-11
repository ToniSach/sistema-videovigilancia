import re, sys, subprocess


def strip_comments(src):
    out = []
    i = 0
    n = len(src)
    state = 'code'
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ''
        if state == 'code':
            if c == '/' and nxt == '/':
                state = 'line'; i += 2; continue
            if c == '/' and nxt == '*':
                state = 'block'; i += 2; continue
            if c == '"':
                if src[i:i + 3] == '"""':
                    out.append('"""'); i += 3; state = 'tstr'; continue
                out.append(c); i += 1; state = 'str'; continue
            out.append(c); i += 1; continue
        elif state == 'line':
            if c == '\n':
                out.append('\n'); state = 'code'
            i += 1; continue
        elif state == 'block':
            if c == '*' and nxt == '/':
                state = 'code'; i += 2; continue
            i += 1; continue
        elif state == 'str':
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(src[i + 1]); i += 2; continue
            if c == '"':
                state = 'code'
            i += 1; continue
        elif state == 'tstr':
            if src[i:i + 3] == '"""':
                out.append('"""'); i += 3; state = 'code'; continue
            out.append(c); i += 1; continue
    code = ''.join(out)
    code = re.sub(r'\s+', ' ', code).strip()
    return code


if __name__ == '__main__':
    files = sys.argv[1:]
    if not files:
        out = subprocess.run(['git', 'diff', '--name-only', 'HEAD', '--', 'CamLink_app/'],
                             capture_output=True, text=True).stdout
        files = [l for l in out.splitlines() if l.strip().endswith('.kt')]
    ident = 0
    changed = []
    newf = []
    for f in files:
        f = f.strip()
        r = subprocess.run(['git', 'show', f'HEAD:{f}'], capture_output=True)
        if r.returncode != 0:
            newf.append(f); continue
        head = r.stdout.decode('utf-8', 'replace')
        cur = open(f, encoding='utf-8').read()
        if strip_comments(head) == strip_comments(cur):
            ident += 1
        else:
            changed.append(f)
    print(f'Modificados: {len(files)} | Solo comentarios (codigo identico): {ident} | '
          f'Codigo cambiado: {len(changed)} | Nuevos: {len(newf)}')
    for f in changed:
        print('  REVISAR:', f)
    for f in newf:
        print('  NUEVO:', f)

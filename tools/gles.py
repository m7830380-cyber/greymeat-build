"""Convert a Unity 6000.6 Shader typetree with DX11 programs to an OpenGL ES 3 (Android) Shader typetree.

Format measured from the Android shell's own shaders (unity_builtin_extra, platform 9):
  * GPU program type 4 (GLES3), text code: "#ifdef VERTEX\\n<vs>\\n#endif\\n#ifdef FRAGMENT\\n<fs>\\n#endif\\n";
  * the whole linked program lives in progVertex (tiers 0..2, slot 3 empty); progFragment has no programs;
  * every cbuffer is flattened into default-block uniforms listed as one "$Globals" constant buffer,
    textures are bound to their UNITY_LOCATION texture unit (hlslcc_zcmp* compare samplers are not listed).
DX11 has separate vertex / pixel variant lists, so every compatible (vertex, pixel) pair becomes one GLES
program whose keyword set is the union of both.
"""
import os, re, copy, hashlib
import lz4.block
import convert_shader as cs
from ukblob import read_params, write_params, read_sub, write_sub
from dx2metal import merge_params, prep_dxbc

PLATFORM_GLES3 = 9
GLES3 = 4
FLAGS = 0x20000 | 0x800 | 0x8000000  # TRANSLATE_MATRICES (Unity's hlslcc_mtx4x4 uniforms) | DISABLE_GLOBALS_STRUCT (plain uniforms)
# | FORCE_EXPLICIT_LOCATIONS: without it ES 3.0 output has no UNITY_LOCATION on samplers -> no texture binds at all
TIER_SLOTS = 3
MAX_COMBOS = 4096        # per pass; vertex-only keywords multiply the programs


class Batch(cs.HlslccBatch):
    def _line(self, key):
        p = os.path.join(self.dir, key)
        return f'{p}.dxbc\t{p}.metal\t{p}.json\t{FLAGS}\tes300\n'


# ---------------- GLSL fixes ----------------
_ARG = cs._ARG
OCC_DOT = re.compile(rb'dot\(\s*' + _ARG + rb',\s*unity_OcclusionMaskSelector\s*\)')
LPPV_ON = re.compile(rb'unity_ProbeVolumeParams\.x\s*==\s*1\.0')
SHADOW_LOD = re.compile(rb'textureLod\((hlslcc_zcmp\w+),\s*' + _ARG + rb',\s*0\.0\)')
LIGHT_TEX = re.compile(rb'(?<!\w)texture(?:Lod|Bias)?\(\s*(?:_LightTexture0|_LightTextureB0)\s*,')


FALLOFF_COORD = re.compile(rb'^\s*(?:[\w.\[\]]+\.([xyzw])\1|vec2\([^,]*\))\s*$')
# Unity's light falloff texture (LightAttenuateNormalized): 1/(1+25 d^2), faded to 0 over the last 20% of range
FALLOFF_FN = (b'vec4 gm_falloff(vec2 c) { float d = c.x; '
              b'return vec4(clamp((1.0 - d) / 0.36, 0.0, 1.0) / (1.0 + 25.0 * d)); }\n')


def atten_falloff(src):
    """The falloff lookups (_LightTextureB0, and _LightTexture0 of a cookie-less point light, both sampled at
    (d^2, d^2)) come back as (0,0,0,1) on the GLES player: the built-in falloff texture never reaches the shader,
    so spot / point lights lit nothing (.x) or lit their whole cone at full strength (.w). Compute the same curve
    instead. Cookie lookups (_LightTexture0 at .xy / .xyz, read from .w) stay texture reads."""
    out, pos = [], 0
    for m in LIGHT_TEX.finditer(src):
        if m.start() < pos:
            continue
        depth, i = 1, m.end()
        while depth:
            c = src[i:i + 1]
            depth += (c == b'(') - (c == b')')
            i += 1
        args = src[m.end():i - 1]
        if b'_LightTextureB0' in m.group(0) or FALLOFF_COORD.match(args):
            out.append(src[pos:m.start()] + b'gm_falloff(' + args + b')')
        else:
            out.append(src[pos:i])
        pos = i
    if not out:
        return src
    src = b''.join(out) + src[pos:]
    if b'gm_falloff(' in src:
        k = src.index(b'\nvoid main()')
        src = src[:k + 1] + FALLOFF_FN + src[k + 1:]
    return src


def fix_glsl(src):
    # same as convert_shader.no_lppv: the DX11 programs multiply every realtime light by the LPPV occlusion
    # mask selector, which the mobile players never set (-> 0, lights light nothing)
    src = OCC_DOT.sub(b'1.0', src)
    src = LPPV_ON.sub(b'false', src)
    # SampleCmpLevelZero: textureLod on a shadow sampler is not ES 3.0 (GL_EXT_texture_shadow_lod); the
    # shadow map has one mip, so a plain compare lookup is identical
    src = SHADOW_LOD.sub(lambda m: b'texture(' + m.group(1) + b', ' + m.group(2) + b')', src)
    return atten_falloff(src)


TEX_DECL = re.compile(rb'^UNITY_LOCATION\((\d+)\) uniform ((?:(?:highp|mediump|lowp) )?\w+) (\w+);', re.M)
UNI_DECL = re.compile(rb'^uniform\s+(?:(?:highp|mediump|lowp)\s+)?(\w+)\s+(\w+)(?:\[(\d+)\])?;', re.M)
VS_OUT = re.compile(rb'^((?:(?:flat|noperspective|centroid|smooth) )*)out ((?:highp |mediump |lowp )?)(\w+) (vs_\w+);', re.M)
FS_IN = re.compile(rb'^((?:(?:flat|noperspective|centroid|smooth) )*)in ((?:highp |mediump |lowp )?)\s*(\w+) +(vs_\w+);', re.M)
VEC_N = {b'float': 1, b'int': 1, b'uint': 1, b'bool': 1}
for _p in (b'vec', b'ivec', b'uvec', b'bvec'):
    for _n in (2, 3, 4):
        VEC_N[_p + str(_n).encode()] = _n


def _base(t):
    return t.rstrip(b'234').replace(b'vec', b'') or b'float'


def _comp(t):
    """GLSL scalar/vector type -> (scalar kind, n)."""
    if t in (b'float', b'int', b'uint', b'bool'):
        return t, 1
    k = {b'vec': b'float', b'ivec': b'int', b'uvec': b'uint', b'bvec': b'bool'}[t[:-1]]
    return k, int(t[-1:])


def _vtype(kind, n):
    if n == 1:
        return kind
    return {b'float': b'vec', b'int': b'ivec', b'uint': b'uvec', b'bool': b'bvec'}[kind] + str(n).encode()


def match_varyings(vs, fs, log):
    """Make the vertex outputs exactly match the fragment inputs (GLSL ES links by name and type)."""
    outs = {m.group(4): m for m in VS_OUT.finditer(vs)}
    fixes = []
    for m in FS_IN.finditer(fs):
        qual, prec, ftype, name = m.group(1), m.group(2), m.group(3), m.group(4)
        o = outs.get(name)
        if o is not None and o.group(3) == ftype and o.group(1).strip() == qual.strip():
            continue
        fixes.append((qual, prec or b'highp ', ftype, name, o))
    if not fixes:
        return vs
    for qual, prec, ftype, name, o in fixes:
        fk, fn = _comp(ftype)
        tmp = name + b'_xlv'
        if o is not None:
            vk, vn = _comp(o.group(3))
            # vertex keeps writing its own type into a private copy; the real output is assigned at return
            decl = o.group(0)
            vs = re.sub(rb'\b' + name + rb'\b', tmp, vs)
            vs = vs.replace(re.sub(rb'\b' + name + rb'\b', tmp, decl),
                            o.group(2) + o.group(3) + b' ' + tmp + b';\n' + qual + b'out ' + prec + ftype + b' ' + name + b';', 1)
            if fn <= vn:
                val = tmp + (b'.' + b'xyzw'[:fn] if vn > 1 and fn < vn else b'')
            else:
                val = tmp + b', ' + b', '.join([b'0.0' if fk == b'float' else b'0'] * (fn - vn))
            if vk != fk or fn > vn:
                val = _vtype(fk, fn) + b'(' + val + b')'
        else:
            vs = re.sub(rb'(\nin [^\n]*;\n)(?!in )', lambda mm: mm.group(1) + qual + b'out ' + prec + ftype + b' ' + name + b';\n', vs, 1)
            val = _vtype(fk, fn) + b'(0)'
        vs = re.sub(rb'(\n\s*)return;', lambda mm, a=name + b' = ' + val + b';': mm.group(1) + a + mm.group(1) + b'return;', vs)
    log('    varyings fixed: %s' % b', '.join(f[3] for f in fixes).decode())
    return vs


# ---------------- linking ----------------
def link(v, f, log):
    """v / f: dict(glsl, refl, P, sub, ent). -> (code bytes, params segment, used names)."""
    vs, fs = fix_glsl(v['glsl']), fix_glsl(f['glsl'])
    # texture units are per program: vertex keeps its units, fragment textures move after them
    # a shadow compare sampler (hlslcc_zcmpX) shares the texture unit of X (Unity binds only X)
    key = lambda n: n[len(b'hlslcc_zcmp'):] if n.startswith(b'hlslcc_zcmp') else n
    units = {}
    nxt = 0

    def remap(m):
        nonlocal nxt
        k = key(m.group(3))
        if k not in units:
            units[k] = nxt
            nxt += 1
        return b'UNITY_LOCATION(%d) uniform %s %s;' % (units[k], m.group(2), m.group(3))
    vs = TEX_DECL.sub(remap, vs)
    fs = TEX_DECL.sub(remap, fs)
    vs = match_varyings(vs, fs, log)

    texinfo = {}
    for t in v['P']['tex'] + f['P']['tex']:
        texinfo.setdefault(t['name'], t)
    binds = []
    for name, u in sorted(units.items(), key=lambda kv: kv[1]):
        n = name.decode()
        if n.startswith('hlslcc_zcmp'):
            continue
        t = texinfo.get(n)
        if t is None:
            raise cs.ConvertError('unknown texture %s' % n)
        binds.append(dict(kind='tex', name=n, index=u, sampler=u, extra=(t['dim'] << 1) | (t['ms'] & 1)))

    vars_ = {}
    for P in (v['P'], f['P']):
        for cn, cb in P['cbs'].items():
            for x in cb['vars']:
                vars_.setdefault(x['name'], x)
    params, seen, off = [], set(), 0
    for src in (vs, fs):
        for m in UNI_DECL.finditer(src):
            typ, gname, arr = m.group(1), m.group(2).decode(), m.group(3)
            if typ.startswith((b'sampler', b'isampler', b'usampler')):
                continue
            mm = re.match(r'hlslcc_mtx(\d)x(\d)(\w+)$', gname)
            name = mm.group(3) if mm else gname
            if name in seen:
                continue
            seen.add(name)
            x = vars_.get(name)
            if x is None:
                raise cs.ConvertError('unknown uniform %s' % name)
            if x.get('struct'):
                raise cs.ConvertError('struct uniform %s' % name)
            params.append(dict(name=name, type=x['type'], rows=x['rows'], cols=x['cols'], isMatrix=x['isMatrix'],
                               arraySize=x['arraySize'], index=off))
            off += 16 * (x['rows'] if x['isMatrix'] else 1) * max(1, x['arraySize'])
    cbs = [dict(name='', size=0, params=[], structs=[])]
    if params:
        cbs.append(dict(name='$Globals', size=off, params=params, structs=[]))
        binds.append(dict(kind='cb', name='$Globals', index=-1, arraySize=1))
    pseg = write_params(dict(version=cs.BLOB_VERSION, cbs=cbs, binds=binds, tail=b''))
    code = b'#ifdef VERTEX\n' + vs + b'\n#endif\n#ifdef FRAGMENT\n' + fs + b'\n#endif\n'
    used = ['$Globals'] if params else []
    used += [p['name'] for p in params] + [b['name'] for b in binds if b['kind'] == 'tex']
    return code, pseg, used


def source_map(refl):
    """Vertex inputs (in_POSITION0, in_TEXCOORD1, ...) -> Unity vertex channel bit mask."""
    smap = 0
    for r in refl['refl']:
        if r['k'] != 'input':
            continue
        m = re.match(r'(?:in_)?([A-Z_]+?)(\d*)$', r['name'])
        if m is None:
            raise cs.ConvertError('vertex input %s' % r['name'])
        sem, idx = m.group(1), int(m.group(2) or 0)
        if sem == 'TEXCOORD':
            smap |= 1 << (4 + idx)
        elif sem in cs.SEM_SRC:
            smap |= 1 << cs.SEM_SRC[sem]
        else:
            raise cs.ConvertError('unknown vertex semantic %s' % r['name'])
    return smap


def pair_variants(vl, fl):
    """vl / fl: [(frozenset keyword indices, item)] -> [(keyword set, v item, f item)]."""
    kv = frozenset().union(*[k for k, _ in vl]) if vl else frozenset()
    kf = frozenset().union(*[k for k, _ in fl]) if fl else frozenset()
    out, seen = [], set()
    for fk, fi in fl:
        cand = [(vk, vi) for vk, vi in vl if vk & kf == fk & kv]
        if not cand:
            cand = [max(vl, key=lambda x: (len(x[0] & fk), -len(x[0] ^ fk)))]
        for vk, vi in cand:
            k = vk | fk
            if k in seen:
                continue
            seen.add(k)
            out.append((k, vi, fi))
    return out


def convert_shader(tt, blob, workdir, log=print):
    if cs.PLATFORM_D3D11 not in tt['platforms']:
        return None
    pi = tt['platforms'].index(cs.PLATFORM_D3D11)
    chunks = cs.platform_chunks(tt, blob, pi)
    ents = cs.entries_of(chunks)
    name = tt['m_ParsedForm']['m_Name']
    batch = Batch(workdir)
    stats = dict(ok=0, fail=0, dropped=0, programs=0)
    layouts = cs.shared_cb_layouts(tt, chunks, ents, name, log)
    items = {}        # key -> dict(P, sub, ent, is_vs)
    by_digest = {}
    pass_lists = {}   # (si, pa) -> (vl, fl)
    for si, ss in enumerate(tt['m_ParsedForm']['m_SubShaders']):
        for pa, p in enumerate(ss['m_Passes']):
            names = {v: k for k, v in p['m_NameIndices']}
            vl, fl, seen = [], [], set()
            for prog in ('progVertex', 'progFragment'):
                pr = p[prog]
                for slot, lst in enumerate(pr['m_PlayerSubPrograms']):
                    pbl = pr['m_ParameterBlobIndices'][slot] if slot < len(pr['m_ParameterBlobIndices']) else []
                    for ei, e in enumerate(lst):
                        t = e['m_GpuProgramType']
                        if t not in cs.DX_VS | cs.DX_PS:
                            continue
                        pbi = pbl[ei] if pbl else -1
                        ck = (prog, e['m_BlobIndex'], pbi)
                        if ck in seen:
                            continue
                        seen.add(ck)
                        try:
                            bp = read_params(cs.get_seg(chunks, ents[pbi])) if pbi >= 0 else None
                            P = merge_params(pr['m_CommonParameters'], names, bp)
                            for cn, cb in P['cbs'].items():
                                if cn in layouts:
                                    cb['vars'] = [dict(x) for x in layouts[cn]['vars']]
                                    cb['size'] = layouts[cn]['size']
                            sub = read_sub(cs.get_seg(chunks, ents[e['m_BlobIndex']]))
                            dx, _ = prep_dxbc(sub['code'], P)
                            dig = hashlib.sha1(dx).hexdigest()
                            key = by_digest.get(dig)
                            if key is None:
                                key = by_digest[dig] = 'k%d' % len(by_digest)
                                batch.add(key, dx)
                                items[key] = dict(P=P, sub=sub, is_vs=t in cs.DX_VS)
                            (vl if t in cs.DX_VS else fl).append((frozenset(e['m_KeywordIndices']), (key, e)))
                        except Exception as ex:
                            stats['fail'] += 1
                            log('  [%s] prep failed: %r' % (name, ex))
            needs_missing = any(e['m_GpuProgramType'] in cs.DX_TYPES - cs.DX_VS - cs.DX_PS
                                for prog in cs.PROGS for lst in p[prog]['m_PlayerSubPrograms'] for e in lst)
            if needs_missing:
                stats['dropped'] += 1
                vl, fl = [], []
            pass_lists[(si, pa)] = (vl, fl)
    batch.run()
    for key, it in items.items():
        glsl, refl = batch.result(key)
        it['glsl'], it['refl'] = glsl, refl
        if glsl is None:
            stats['fail'] += 1
            log('  [%s] hlslcc failed%s' % (name, (': ' + str([d['msg'] for d in refl['refl'] if d['k'] == 'diag'])[:300]) if refl else ' (crash)'))

    kwnames = tt['m_ParsedForm']['m_KeywordNames']
    segs, linked = [], {}
    nt = copy.deepcopy(tt)
    for si, ss in enumerate(nt['m_ParsedForm']['m_SubShaders']):
        for pa, p in enumerate(ss['m_Passes']):
            if p.get('m_Platforms'):
                p['m_Platforms'] = [PLATFORM_GLES3] if cs.PLATFORM_D3D11 in p['m_Platforms'] else []
            vl, fl = pass_lists[(si, pa)]
            vl = [x for x in vl if items[x[1][0]]['glsl'] is not None]
            fl = [x for x in fl if items[x[1][0]]['glsl'] is not None]
            combos = pair_variants(vl, fl)
            if len(combos) > MAX_COMBOS:
                log('  [%s] pass %d: %d programs, keeping %d' % (name, pa, len(combos), MAX_COMBOS))
                combos = combos[:MAX_COMBOS]
            subs, pars, pass_names = [], [], []
            for kw, (vk, ve), (fk, fe) in combos:
                lk = (vk, fk)
                if lk not in linked:
                    try:
                        v, f = items[vk], items[fk]
                        code, pseg, used = link(v, f, lambda s: log('  [%s]%s' % (name, s)))
                        smap = source_map(v['refl'])
                        kws = list(dict.fromkeys(v['sub']['keywords'] + f['sub']['keywords']))
                        gsub = dict(version=cs.BLOB_VERSION, type=GLES3, stats=[0, 0, 0, 0], keywords=kws,
                                    code=code, sourceMap=smap, channels=[], tail=b'')
                        segs.append(pseg)
                        segs.append(write_sub(gsub))
                        linked[lk] = (len(segs) - 1, len(segs) - 2, used)
                        stats['ok'] += 1
                    except Exception as ex:
                        linked[lk] = None
                        stats['fail'] += 1
                        log('  [%s] link failed: %r' % (name, ex))
                r = linked[lk]
                if r is None:
                    continue
                ne = dict(ve)
                ne['m_BlobIndex'] = r[0]
                ne['m_GpuProgramType'] = GLES3
                ne['m_KeywordIndices'] = sorted(kw)
                ne['m_ShaderRequirements'] = ve['m_ShaderRequirements'] | fe['m_ShaderRequirements']
                subs.append(ne)
                pars.append(r[1])
                pass_names += r[2]
            stats['programs'] += len(subs)
            for prog in cs.PROGS:
                pr = p[prog]
                if prog == 'progVertex' and pr['m_PlayerSubPrograms']:
                    pr['m_PlayerSubPrograms'] = [list(subs) for _ in range(TIER_SLOTS)] + [[]]
                    pr['m_ParameterBlobIndices'] = [list(pars) for _ in range(TIER_SLOTS)] + [[]]
                else:
                    pr['m_PlayerSubPrograms'] = []
                    pr['m_ParameterBlobIndices'] = []
                for k in pr['m_CommonParameters']:
                    pr['m_CommonParameters'][k] = []
            have = {n for n, _ in p['m_NameIndices']}
            nxt = max([i for _, i in p['m_NameIndices']] + [-1]) + 1
            for n in dict.fromkeys(pass_names):
                if n not in have:
                    p['m_NameIndices'].append((n, nxt))
                    have.add(n)
                    nxt += 1

    raw = cs.pack_platform(segs)
    comp = [lz4.block.compress(c, store_size=False, mode='high_compression') for c in raw]
    offs, o = [], 0
    for c in comp:
        offs.append(o)
        o += len(c)
    nt['platforms'] = [PLATFORM_GLES3]
    nt['offsets'] = [offs]
    nt['compressedLengths'] = [[len(c) for c in comp]]
    nt['decompressedLengths'] = [[len(c) for c in raw]]
    nt['stageCounts'] = [tt['stageCounts'][pi]] if tt.get('stageCounts') else []
    nt['compressedBlob'] = b''.join(comp)
    log('  [%s] programs=%d linked ok=%d fail=%d dropped passes=%d' % (name, stats['programs'], stats['ok'], stats['fail'], stats['dropped']))
    return nt, stats

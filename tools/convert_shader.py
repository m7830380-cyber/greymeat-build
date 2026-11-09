"""Convert a Unity 6 (6000.0) Shader typetree with DX11 programs to a Metal-only Shader typetree.

Unity 6 layout (same blob version as 2022.3, see ukblob.py):
  * each program stage has m_PlayerSubPrograms[4] (one list per graphics tier; Windows uses only slot 3,
    iOS players look in slots 0..2) and the parallel m_ParameterBlobIndices[4];
  * every entry points (m_BlobIndex) at a code segment and (parameter blob index) at a parameter segment of
    the per-platform blob; shared cbuffer layouts / bindings live in m_CommonParameters.
DX11 vertex/pixel programs are translated with HLSLcc (hlslcc_cli) to MSL. For every translated program a
self-contained Metal parameter segment is written (Metal cbuffer names / offsets differ from DX11), so the
common parameters are cleared. Geometry/hull/domain programs have no Metal equivalent and are dropped.
The Metal code header (0xF00DCAFE, version 7) and its flags were measured from the shell's own Unity 6
iOS shaders (unity_builtin_extra): 37 normally, 39 for vertex programs that write render_target_array_index.
"""
import os, re, json, struct, subprocess, copy, hashlib
import lz4.block
from ukblob import read_params, write_params, read_sub, write_sub
from dx2metal import merge_params, prep_dxbc, var_size

HERE = os.path.dirname(os.path.abspath(__file__))
_UK_HLSLCC = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'ultrakill-ios-port', 'tools', 'hlslcc_cli',
                          'build', 'Release', 'hlslcc_cli.exe')
HLSLCC = os.environ.get('HLSLCC_CLI') or next(
    (p for p in (os.path.join(HERE, 'hlslcc_cli', 'build', 'Release', 'hlslcc_cli.exe'), _UK_HLSLCC) if os.path.exists(p)),
    'hlslcc_cli')
BLOB_VERSION = 202605290   # 0x0C1382EA, Unity 6000.6
PLATFORM_D3D11, PLATFORM_METAL = 4, 14
DX_TYPES = set(range(15, 23))
DX_VS = {15, 16}
DX_PS = {17, 18}
METAL_VS, METAL_FS = 23, 24
HLSLCC_FLAGS = 0x20000 | 0x80000   # TRANSLATE_MATRICES | METAL_SHADOW_SAMPLER_LINEAR
METAL_TIER_SLOTS = 3               # iOS: tiers 0..2 get the same programs, slot 3 stays empty
PROGS = ('progVertex', 'progFragment', 'progGeometry', 'progHull', 'progDomain', 'progRayTracing')
SEM_SRC = {'POSITION': 0, 'NORMAL': 1, 'TANGENT': 2, 'COLOR': 3, 'BLENDWEIGHTS': 12, 'BLENDWEIGHT': 12, 'BLENDINDICES': 13}


class ConvertError(Exception):
    pass


def metal_code(msl, is_vs):
    name = b'xlatMtlMain\0'
    flags = 37
    if is_vs and b'render_target_array_index' in msl:
        flags |= 2
    hdr = struct.pack('<IIIIIIIIIiiI', 0xF00DCAFE, 48, 7, 48 + len(name), 0, 0, 0, 0, 0, flags, -1, 0)
    return hdr + name + msl


# ---------------- platform blob io ----------------
def platform_chunks(tt, blob, pi):
    out = []
    for j in range(len(tt['offsets'][pi])):
        o, cl, dl = tt['offsets'][pi][j], tt['compressedLengths'][pi][j], tt['decompressedLengths'][pi][j]
        out.append(lz4.block.decompress(blob[o:o + cl], uncompressed_size=dl))
    return out


def entries_of(chunks):
    d = chunks[0]
    n = struct.unpack_from('<i', d, 0)[0]
    return [struct.unpack_from('<iii', d, 4 + 12 * k) for k in range(n)]


def get_seg(chunks, ent):
    o, l, s = ent
    return chunks[s][o:o + l]


def pack_platform(segs, chunk_limit=48 << 20):
    """Segments -> raw chunks, entry table (offset, length, chunk) at the start of chunk 0."""
    n = len(segs)
    table = 4 + 12 * n
    chunks = [bytearray(table)]
    ents = []
    for s in segs:
        if len(chunks[-1]) + len(s) > chunk_limit and len(chunks[-1]) > (table if len(chunks) == 1 else 0):
            chunks.append(bytearray())
        ents.append((len(chunks[-1]), len(s), len(chunks) - 1))
        chunks[-1] += s
    struct.pack_into('<i', chunks[0], 0, n)
    for k, e in enumerate(ents):
        struct.pack_into('<iii', chunks[0], 4 + 12 * k, *e)
    return [bytes(c) for c in chunks]


# ---------------- reflection -> metal params ----------------
def metal_layout(c):
    n = max(1, c['arraySize'])
    if c['isMatrix']:
        return 16 * c['cols'] * n, 16
    cols = c['cols']
    if cols == 1:
        return 4 * n, 4
    if cols == 2:
        return 8 * n, 8
    return 16 * n, 16


MSL_SCALAR = {'float': 4, 'int': 4, 'uint': 4, 'bool': 4, 'half': 2, 'short': 2, 'ushort': 2}
MEMBER = re.compile(r'^\s+(packed_)?([a-z]+)(\d?) (\w+)(?:\[(\d+)\])?;\s*$')


def pad_cbuffers(msl, refl, log=None):
    """Unity fills its built-in cbuffers (UnityPerCamera, UnityLighting, UnityPerDraw, ...) as one block with the
    engine's fixed layout - the DX11 offsets. HLSLcc packs only the members a program uses, so on Metal they moved
    (_WorldSpaceCameraPos 64 -> 0 ...) and lights / fog / the camera read the wrong values: everything went dark.
    Rewrite every named cbuffer struct (not the per-stage $Globals = VGlobals / FGlobals) with explicit padding so
    each member sits at its DX offset. Returns (msl, set of padded cbuffer names)."""
    padded = set()
    cbs, cur = [], None
    for r in refl['refl']:
        if r['k'] == 'cb':
            cur = dict(name=r['name'], size=r['size'], consts=[])
            cbs.append(cur)
        elif r['k'] == 'const' and cur is not None:
            cur['consts'].append(r)
    text = msl.decode('utf8')
    for cb in cbs:
        if cb['name'] in ('VGlobals', 'FGlobals', 'Globals'):
            continue
        m = re.search(r'struct %s_Type\n\{\n(.*?)\n\};' % re.escape(cb['name']), text, re.S)
        if not m:
            continue
        offs = {c['name']: c['index'] for c in cb['consts']}
        members = []
        ok = True
        for line in m.group(1).split('\n'):
            mm = MEMBER.match(line)
            if not mm:
                ok = False
                break
            packed, base, n, name, arr = mm.groups()
            key = name[len('hlslcc_mtx4x4'):] if name.startswith('hlslcc_mtx4x4') else name
            if key not in offs or base not in MSL_SCALAR:
                ok = False
                break
            n = int(n or 1)
            members.append([offs[key], base, n, name, int(arr) if arr else 0])
        if not ok or not members:
            if log:
                log('  cbuffer %s: layout not padded' % cb['name'])
            continue
        members.sort(key=lambda x: x[0])
        out, cursor = [], 0
        for i, (o, base, n, name, arr) in enumerate(members):
            sc = MSL_SCALAR[base]
            align = sc * (4 if n == 3 else n)
            size = sc * n if n != 3 else sc * 4
            nxt = members[i + 1][0] if i + 1 < len(members) else None
            typ = '%s%s' % (base, n if n > 1 else '')
            if arr:
                if size < 16 and n != 1 or (n == 1 and arr > 1):
                    ok = False      # DX arrays have a 16-byte stride: not expressible for float/float2 arrays
                    break
                size = size * arr
            elif n > 1 and (o % align or (n == 3 and nxt is not None and nxt < o + size)):
                typ = 'packed_' + typ   # DX packs a float3 + float into one register
                size = sc * n
            if o < cursor:
                ok = False
                break
            if o > cursor:
                out.append('    char _gm_pad%d[%d];' % (o, o - cursor))
            out.append('    %s %s%s;' % (typ, name, '[%d]' % arr if arr else ''))
            cursor = o + size
        if not ok:
            if log:
                log('  cbuffer %s: layout not padded (overlap/array)' % cb['name'])
            continue
        text = text[:m.start(1)] + '\n'.join(out) + text[m.end(1):]
        padded.add(cb['name'])
    return text.encode('utf8'), padded


def build_metal_params(refl, msl, P, cbname_map, fixed=()):
    """HLSLcc reflection + original DX params -> Metal parameter segment.
    fixed: cbuffers whose Metal struct was padded to the DX layout (pad_cbuffers): DX offsets / size are kept."""
    orig_vars = {}
    for cn, cb in P['cbs'].items():
        for v in cb['vars']:
            orig_vars[(cn, v['name'])] = v
    cbs = [dict(name='', size=0, params=[], structs=[])]
    binds = []
    cur = None
    off = 0
    for r in refl['refl']:
        k = r['k']
        if k == 'cb':
            cur = dict(name=r['name'], size=0, params=[], structs=[])
            cbs.append(cur)
            off = 0
        elif k == 'const':
            if cur is None:
                raise ConvertError('const outside cb')
            size, align = metal_layout(r)
            off = (off + align - 1) // align * align
            if cur['name'] in fixed:
                off = r['index']
                size = 0
            ocb = cbname_map.get(cur['name'], cur['name'])
            ov = orig_vars.get((ocb, r['name']))
            if ov is None:
                raise ConvertError('unknown const %s.%s' % (ocb, r['name']))
            if ov.get('struct'):
                st = dict(ov['struct'], index=off)
                cur['structs'].append(st)
                off += st['size'] * max(1, st['arraySize'])
                cur['size'] = off
                continue
            cur['params'].append(dict(name=r['name'], type=ov['type'], rows=ov['rows'], cols=ov['cols'],
                                      isMatrix=ov['isMatrix'], arraySize=ov['arraySize'], index=off))
            off += size
            cur['size'] = off
            if cur['name'] in fixed:
                cur['size'] = next(x['size'] for x in refl['refl'] if x['k'] == 'cb' and x['name'] == cur['name'])
        elif k == 'cbbind':
            binds.append(dict(kind='cb', name=r['name'], index=r['index'], arraySize=1))
        elif k == 'tex':
            if r['name'].startswith('_UnityFBInput') and b'color(xlt_remap_i' in msl:
                continue   # a fragment input on Metal (framebuffer_fetch), not a texture
            if r['uav']:
                ou = next((u for u in P['uav'] if u['name'] == r['name']), None)
                binds.append(dict(kind='uav', name=r['name'], index=r['index'], orig=ou['orig'] if ou else r['index']))
                continue
            ot = next((t for t in P['tex'] if t['name'] == r['name']), None)
            if ot is None:
                raise ConvertError('unknown texture %s' % r['name'])
            samp = r['sampler'] if r['sampler'] >= 0 else -1
            binds.append(dict(kind='tex', name=r['name'], index=r['index'], sampler=samp,
                              extra=(ot['dim'] << 1) | (ot['ms'] & 1)))
        elif k == 'buf':
            if r['uav']:
                ou = next((u for u in P['uav'] if u['name'] == r['name']), None)
                binds.append(dict(kind='uav', name=r['name'], index=r['index'], orig=ou['orig'] if ou else r['index']))
            else:
                ob = next((b for b in P['buf'] if b['name'] == r['name']), None)
                binds.append(dict(kind='buf', name=r['name'], index=r['index'], arraySize=ob['arraySize'] if ob else 0))
    for m in re.finditer(rb'sampler sampler_inline(\d+) \[\[ sampler \((\d+)\) \]\]', msl):
        s = next((x for x in P['samplers'] if x['bind'] == int(m.group(1))), None)
        if s is not None:
            binds.append(dict(kind='sampler', name='', bind=int(m.group(2)), sampler=s['sampler']))
    return write_params(dict(version=BLOB_VERSION, cbs=cbs, binds=binds, tail=b''))


def vertex_channels(refl):
    ch = []
    smap = 0
    for r in refl['refl']:
        if r['k'] != 'input':
            continue
        m = re.match(r'([A-Z_]+?)(\d*)$', r['name'])
        sem, idx = m.group(1), int(m.group(2) or 0)
        if sem == 'TEXCOORD':
            src = 4 + idx
        elif sem in SEM_SRC:
            src = SEM_SRC[sem]
        else:
            raise ConvertError('unknown vertex semantic %s' % r['name'])
        ch.append((src, 13 + r['index']))
        smap |= 1 << src
    return smap, ch


FB_TEX = re.compile(rb'texture2d\w*<(float|half|int|uint), access::\w+ > _UnityFBInput(\d) \[\[ texture\(\d+\) \]\]')


XLT_REMAP_O = re.compile(rb'#ifndef XLT_REMAP_O\n\t#define XLT_REMAP_O \{0, 1, 2, 3, 4, 5, 6, 7\}\n#endif\nconstexpr constant uint xlt_remap_o\[\] = XLT_REMAP_O;\n')


def output_remap(msl):
    """Unity 6000.6 binds fragment outputs through function constant 1 (rp_output_remap_mask, 4 bits per output),
    like its own Metal shaders do; HLSLcc's static identity table would ignore the render pass remapping."""
    used = sorted({int(n) for n in re.findall(rb'xlt_remap_o\[(\d)\]', msl)})
    if not used:
        return XLT_REMAP_O.sub(b'', msl)
    decl = b'constant uint32_t rp_output_remap_mask [[ function_constant(1) ]];\n' + b''.join(
        b'constant const uint rp_output_remap_%d = (rp_output_remap_mask >> %d) & 0xF;\n' % (n, 4 * n) for n in used)
    out, k = XLT_REMAP_O.subn(decl, msl)
    if not k:
        raise ConvertError('xlt_remap_o table not found')
    return re.sub(rb'xlt_remap_o\[(\d)\]', rb'rp_output_remap_\1', out)


def fix_msl(msl, is_vs):
    if is_vs:
        msl = msl.replace(b'[[ position ]]', b'[[ position, invariant ]]')
    else:
        if FB_TEX.search(msl):
            msl = framebuffer_fetch(msl)
        msl = output_remap(msl)
        msl = atten_falloff(msl)
    return shadow_sampler(no_lppv(msl))


MTL_FALLOFF = re.compile(rb'(_LightTextureB0|_LightTexture0)\.sample\(\s*sampler\1\s*,\s*'
                         rb'([\w.\[\]]+\.([xyzw])\3|float2\([^,()]*\))\s*\)')
MTL_FALLOFF_FN = (b'static inline float4 gm_falloff(float2 c) { float d = c.x; '
                  b'return float4(clamp((1.0 - d) / 0.36, 0.0, 1.0) / (1.0 + 25.0 * d)); }\n')


def atten_falloff(msl):
    """Light falloff lookups (_LightTextureB0, and _LightTexture0 of a cookie-less point light, sampled at
    (d^2, d^2)): computed instead of read. Same curve as Unity's built-in falloff texture
    (1/(1+25 d^2), faded to 0 over the last 20% of range); on the GLES player that texture reaches the shader
    as (0,0,0,1) and lamps lit nothing. Cookie lookups (.xy / .xyz coordinates) stay texture reads."""
    msl, n = MTL_FALLOFF.subn(lambda m: b'gm_falloff(' + m.group(2) + b')', msl)
    if n:
        k = msl.index(b'\nfragment ')
        msl = msl[:k + 1] + MTL_FALLOFF_FN + msl[k + 1:]
    return msl


CMP_SAMPLE = re.compile(rb'\.sample_compare\(\s*sampler\w+\s*,')
SHADOW_DECL = (b'constexpr sampler _mtl_xl_shadow_sampler(address::clamp_to_edge, filter::linear, '
               b'compare_func::greater_equal);\n')


def shadow_sampler(msl):
    """HLSLcc without HLSLCC_FLAG_MOBILE_TARGET writes macOS code: sample_compare through the bound sampler, whose
    compare function "is set from the app side". Unity's iOS player sets none (its iOS shaders carry the inline
    _mtl_xl_shadow_sampler), so the compare was MTLCompareFunctionNever, every shadow lookup returned 0 and every
    shadow-casting light (all of the game's lamps) lit nothing. Use the inline sampler like Unity's iOS shaders."""
    msl, n = CMP_SAMPLE.subn(b'.sample_compare(_mtl_xl_shadow_sampler,', msl)
    if n:
        msl = msl.replace(b'using namespace metal;\n', b'using namespace metal;\n' + SHADOW_DECL, 1)
    return msl


_ARG = rb'((?:[^(),;]|\((?:[^()]|\([^()]*\))*\))+)'
OCC_DOT = re.compile(rb'dot\(\s*' + _ARG + rb',\s*UnityLighting\.unity_OcclusionMaskSelector\s*\)')
OCC_DOT2 = re.compile(rb'dot\(\s*UnityLighting\.unity_OcclusionMaskSelector\s*,\s*' + _ARG + rb'\)')
LPPV_ON = re.compile(rb'UnityProbeVolume\.unity_ProbeVolumeParams\.x\s*==\s*1\.0')


def no_lppv(msl):
    """DX11 builds define UNITY_LIGHT_PROBE_PROXY_VOLUME, so every realtime light's attenuation is multiplied by
    saturate(dot(occlusion, unity_OcclusionMaskSelector)) and may sample unity_ProbeVolumeSH. Metal builds don't
    (none of Unity's own Metal shaders read either), so the iOS engine leaves the selector at zero and every
    ForwardAdd light came out black. Without LPPV the occlusion is 1 for realtime lights, as on Windows."""
    msl = OCC_DOT.sub(b'1.0', msl)
    msl = OCC_DOT2.sub(b'1.0', msl)
    return LPPV_ON.sub(b'false', msl)


def framebuffer_fetch(msl):
    """DX11 emulates framebuffer fetch (FRAMEBUFFER_INPUT, used by Hidden/CoreSRP/CoreCopy) with a texture
    _UnityFBInputN read at the pixel position. On Metal the render graph's copy pass gives that attachment to the
    shader as a fragment input instead and binds no texture, so the copy read nothing and the full-screen effects
    after it (fog, scary) drew one flat colour over the whole picture. Rewritten the way HLSLcc writes Unity's own
    Metal framebuffer fetch: a [[ color(xlt_remap_i[N]) ]] parameter (MSAA: the current sample)."""
    msl = FB_TEX.sub(lambda m: b'%s4 _UnityFBInput%s [[ color(xlt_remap_i[%s]) ]]' % (m.group(1), m.group(2), m.group(2)), msl)
    msl = re.sub(rb'_UnityFBInput(\d)\.read\([^();]*\)', rb'_UnityFBInput\1', msl)
    msl = re.sub(rb'_UnityFBInput(\d)\.read\(\s*return', rb'_UnityFBInput\1;\n    return', msl)   # HLSLcc's broken MS-array read
    at = re.search(rb'\n(struct |constant |constexpr |fragment )', msl)
    decl = b'#ifndef XLT_REMAP_I\n\t#define XLT_REMAP_I {0, 1, 2, 3, 4, 5, 6, 7}\n#endif\nconstexpr constant uint xlt_remap_i[] = XLT_REMAP_I;\n'
    return msl[:at.start() + 1] + decl + msl[at.start() + 1:]


# Unity inline sampler flags (sampler_LinearClamp etc.): filter in bits 0-1, wrap U/V/W in bits 2-7.
INLINE_FILTER = {0: b'filter::nearest, mip_filter::nearest', 1: b'filter::linear, mip_filter::nearest',
                 2: b'filter::linear, mip_filter::linear'}
INLINE_SEEN = {}   # flags -> count, reported by convert_data
INLINE_WRAP = {0: b'repeat', 1: b'clamp_to_edge', 2: b'mirrored_repeat', 3: b'mirrored_repeat'}


def inline_samplers(msl, P):
    """Bake HLSL inline samplers into the MSL as constexpr samplers, like Unity's own Metal compiler.
    Unity's Metal runtime does not set up sampler states for a bound slot with no texture behind it,
    so a bound inline sampler falls back to repeat wrapping (curves sampled at 1.0 wrap to 0)."""
    pat = re.compile(rb'(,?)\s*\n\s*sampler sampler_inline(\d+) \[\[ sampler \((\d+)\) \]\](,?)')
    decls = []

    def sub(m):
        s = next((x for x in P['samplers'] if x['bind'] == int(m.group(2))), None)
        flags = s['sampler'] if s else None
        INLINE_SEEN[flags] = INLINE_SEEN.get(flags, 0) + 1
        if flags is None or flags >> 8 or (flags & 3) not in INLINE_FILTER:
            return m.group(0)   # unknown (e.g. comparison sampler): leave it bound
        w = [INLINE_WRAP[(flags >> sh) & 3] for sh in (2, 4, 6)]
        decls.append(b'constexpr sampler sampler_inline%s(%s, s_address::%s, t_address::%s, r_address::%s);\n'
                     % (m.group(2), INLINE_FILTER[flags & 3], w[0], w[1], w[2]))
        return b',' if m.group(1) and m.group(4) else b''
    out = pat.sub(sub, msl)
    if not decls:
        return msl
    at = re.search(rb'\n(vertex|fragment|kernel) ', out)
    return out[:at.start() + 1] + b''.join(decls) + out[at.start() + 1:]


# ---------------- hlslcc batch ----------------
class HlslccBatch:
    def __init__(self, workdir):
        self.dir = workdir
        os.makedirs(workdir, exist_ok=True)
        self.jobs = []

    def add(self, key, dxbc):
        open(os.path.join(self.dir, key) + '.dxbc', 'wb').write(dxbc)
        self.jobs.append(key)

    def _line(self, key):
        p = os.path.join(self.dir, key)
        return f'{p}.dxbc\t{p}.metal\t{p}.json\t{HLSLCC_FLAGS}\n'

    def run(self, nproc=None):
        if not self.jobs:
            return
        nproc = max(1, min(nproc or (os.cpu_count() or 2) - 1, len(self.jobs)))
        procs = []
        for i in range(nproc):
            lf = os.path.join(self.dir, 'list%d.txt' % i)
            with open(lf, 'w') as f:
                f.writelines(self._line(k) for k in self.jobs[i::nproc])
            procs.append(subprocess.Popen([HLSLCC, lf], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL))
        for p in procs:
            p.wait()
        for key in self.jobs:   # a crash kills the rest of its list: retry those one by one
            if not os.path.exists(os.path.join(self.dir, key) + '.json'):
                lf = os.path.join(self.dir, 'single.txt')
                with open(lf, 'w') as f:
                    f.write(self._line(key))
                subprocess.run([HLSLCC, lf], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

    def result(self, key):
        p = os.path.join(self.dir, key)
        if not os.path.exists(p + '.json'):
            return None, None
        refl = json.loads(open(p + '.json', 'rb').read().decode('utf8', 'replace'))
        if not refl['ok']:
            return None, refl
        return open(p + '.metal', 'rb').read(), refl


# ---------------- shared cbuffer layouts ----------------
def shared_cb_layouts(tt, chunks, ents, name, log):
    """Named cbuffer (UnityPerDraw, UnityPerMaterial, ...) -> the union of its variables over every DX11
    program of the shader. Unity's DX11 parameters list only what each program uses, and HLSLcc packs a Metal
    cbuffer struct from what it is told, so the vertex and fragment programs of one pass would disagree on
    member offsets - the SRP Batcher (and BatchRendererGroup / GPU Resident Drawer, which then draws the
    error shader) rejects that ("Builtin property offset in cbuffer overlap other stages"). Declaring the
    same full variable list in every program gives identical structs everywhere.
    $Globals stays per program (it is per stage on Metal and not used by the batcher). A cbuffer whose
    variants disagree (same name at another offset, or overlapping variables) is left alone."""
    union, bad = {}, set()
    for ss in tt['m_ParsedForm']['m_SubShaders']:
        for p in ss['m_Passes']:
            names = {v: k for k, v in p['m_NameIndices']}
            for prog in PROGS:
                pr = p[prog]
                for slot, lst in enumerate(pr['m_PlayerSubPrograms']):
                    pbl = pr['m_ParameterBlobIndices'][slot] if slot < len(pr['m_ParameterBlobIndices']) else []
                    for ei, e in enumerate(lst):
                        if e['m_GpuProgramType'] not in DX_VS | DX_PS:
                            continue
                        pbi = pbl[ei] if pbl else -1
                        try:
                            bp = read_params(get_seg(chunks, ents[pbi])) if pbi >= 0 else None
                            P = merge_params(pr['m_CommonParameters'], names, bp)
                        except Exception:
                            continue
                        for cn, cb in P['cbs'].items():
                            if cn == '$Globals':
                                continue
                            u = union.setdefault(cn, dict(size=0, vars={}))
                            u['size'] = max(u['size'], cb['size'])
                            for v in cb['vars']:
                                o = u['vars'].setdefault(v['name'], v)
                                if (o['offset'], o['cols'], o['rows'], o['isMatrix'], o['arraySize']) != \
                                        (v['offset'], v['cols'], v['rows'], v['isMatrix'], v['arraySize']):
                                    bad.add(cn)
    out = {}
    for cn, u in union.items():
        vs = sorted(u['vars'].values(), key=lambda v: v['offset'])
        if cn not in bad and any(a['offset'] + var_size(a) > b['offset'] for a, b in zip(vs, vs[1:])):
            bad.add(cn)
        if cn in bad:
            log('  [%s] cbuffer %s: variants disagree, layout left per program' % (name, cn))
            continue
        out[cn] = dict(size=u['size'], vars=vs)
    return out


# ---------------- main conversion ----------------
def convert_shader(tt, blob, workdir, log=print):
    """tt: Shader typetree (compressedBlob emptied), blob: compressed blob bytes.
    Returns (new typetree incl. compressedBlob, stats) or None when the shader has no DX11 programs."""
    if PLATFORM_D3D11 not in tt['platforms']:
        return None
    pi = tt['platforms'].index(PLATFORM_D3D11)
    chunks = platform_chunks(tt, blob, pi)
    ents = entries_of(chunks)
    name = tt['m_ParsedForm']['m_Name']
    batch = HlslccBatch(workdir)
    plan = []       # (key, P, sub, is_vs)
    keymap = {}     # (subshader, pass, prog, blob index, param index) -> key or None
    by_digest = {}  # sha1 of the prepared DXBC -> key
    stats = dict(ok=0, fail=0, dropped=0)
    layouts = shared_cb_layouts(tt, chunks, ents, name, log)
    for si, ss in enumerate(tt['m_ParsedForm']['m_SubShaders']):
        for pa, p in enumerate(ss['m_Passes']):
            names = {v: k for k, v in p['m_NameIndices']}
            for prog in PROGS:
                pr = p[prog]
                for slot, lst in enumerate(pr['m_PlayerSubPrograms']):
                    for ei, e in enumerate(lst):
                        t = e['m_GpuProgramType']
                        if t not in DX_TYPES:
                            continue
                        if t not in DX_VS | DX_PS:
                            stats['dropped'] += 1
                            continue
                        pbl = pr['m_ParameterBlobIndices'][slot] if slot < len(pr['m_ParameterBlobIndices']) else []
                        pbi = pbl[ei] if pbl else -1
                        ck = (si, pa, prog, e['m_BlobIndex'], pbi)
                        if ck in keymap:
                            continue
                        key = 'k%d' % len(keymap)
                        keymap[ck] = key
                        try:
                            bp = read_params(get_seg(chunks, ents[pbi])) if pbi >= 0 else None
                            P = merge_params(pr['m_CommonParameters'], names, bp)
                            for cn, cb in P['cbs'].items():
                                if cn in layouts:
                                    cb['vars'] = [dict(v) for v in layouts[cn]['vars']]
                                    cb['size'] = layouts[cn]['size']
                            sub = read_sub(get_seg(chunks, ents[e['m_BlobIndex']]))
                            dx, _ = prep_dxbc(sub['code'], P)
                            dig = hashlib.sha1(dx).hexdigest()   # passes often share identical programs
                            if dig in by_digest:
                                keymap[ck] = by_digest[dig]
                                continue
                            by_digest[dig] = key
                            batch.add(key, dx)
                            plan.append((key, P, sub, t in DX_VS))
                        except Exception as ex:
                            keymap[ck] = None
                            stats['fail'] += 1
                            log('  [%s] prep failed: %r' % (name, ex))
    batch.run()

    segs = []
    results = {}    # key -> (code segment index, param segment index)
    for key, P, sub, is_vs in plan:
        msl, refl = batch.result(key)
        if msl is None:
            stats['fail'] += 1
            log('  [%s] hlslcc failed%s' % (name, (': ' + str([d['msg'] for d in refl['refl'] if d['k'] == 'diag'])[:300]) if refl else ' (crash)'))
            continue
        try:
            msl = inline_samplers(fix_msl(msl, is_vs), P)
            msl, fixed = pad_cbuffers(msl, refl, lambda s: log('  [%s]%s' % (name, s)))
            pseg = build_metal_params(refl, msl, P, {'VGlobals': '$Globals'} if is_vs else {'FGlobals': '$Globals'}, fixed)
            smap, ch = vertex_channels(refl) if is_vs else (0, [])
            msub = dict(version=BLOB_VERSION, type=METAL_VS if is_vs else METAL_FS, stats=[0, 0, 0, 0],
                        keywords=sub['keywords'], code=metal_code(msl, is_vs), sourceMap=smap, channels=ch, tail=b'')
            segs.append(pseg)
            segs.append(write_sub(msub))
            mp = read_params(pseg)
            used = [cb['name'] for cb in mp['cbs'] if cb['name']] + [v['name'] for cb in mp['cbs'] for v in cb['params']] + [
                b['name'] for b in mp['binds'] if b['name']]
            results[key] = (len(segs) - 1, len(segs) - 2, used)
            stats['ok'] += 1
        except Exception as ex:
            stats['fail'] += 1
            log('  [%s] build failed: %r' % (name, ex))

    nt = copy.deepcopy(tt)
    for si, ss in enumerate(nt['m_ParsedForm']['m_SubShaders']):
        for pa, p in enumerate(ss['m_Passes']):
            if p.get('m_Platforms'):
                p['m_Platforms'] = [PLATFORM_METAL] if PLATFORM_D3D11 in p['m_Platforms'] else []
            # A pass that needs geometry/tessellation stages cannot run without them: leave it without programs,
            # as Unity's own Metal build does (the subshader is then skipped / falls back).
            needs_missing = any(e['m_GpuProgramType'] in DX_TYPES - DX_VS - DX_PS
                                for prog in PROGS for lst in p[prog]['m_PlayerSubPrograms'] for e in lst)
            pass_names = []
            for prog in PROGS:
                pr = p[prog]
                subs, pars = [], []
                for slot, lst in enumerate(pr['m_PlayerSubPrograms'] if not needs_missing else []):
                    pbl = pr['m_ParameterBlobIndices'][slot] if slot < len(pr['m_ParameterBlobIndices']) else []
                    for ei, e in enumerate(lst):
                        if e['m_GpuProgramType'] not in DX_VS | DX_PS:
                            continue
                        key = keymap.get((si, pa, prog, e['m_BlobIndex'], pbl[ei] if pbl else -1))
                        r = results.get(key) if key else None
                        if r is None:
                            continue
                        ne = dict(e)
                        ne['m_BlobIndex'] = r[0]
                        ne['m_GpuProgramType'] = METAL_VS if e['m_GpuProgramType'] in DX_VS else METAL_FS
                        subs.append(ne)
                        pars.append(r[1])
                        pass_names += r[2]
                if pr['m_PlayerSubPrograms']:
                    pr['m_PlayerSubPrograms'] = [list(subs) for _ in range(METAL_TIER_SLOTS)] + [[]]
                    pr['m_ParameterBlobIndices'] = [list(pars) for _ in range(METAL_TIER_SLOTS)] + [[]]
                for k in pr['m_CommonParameters']:
                    pr['m_CommonParameters'][k] = []

            # Unity's Metal shaders list their Metal cbuffer names (VGlobals / FGlobals) here as well
            have = {n for n, _ in p['m_NameIndices']}
            nxt = max([i for _, i in p['m_NameIndices']] + [-1]) + 1
            for n in dict.fromkeys(pass_names):
                if n not in have:
                    p['m_NameIndices'].append((n, nxt))
                    have.add(n)
                    nxt += 1

    raw = pack_platform(segs)
    comp = [lz4.block.compress(c, store_size=False, mode='high_compression') for c in raw]
    offs, o = [], 0
    for c in comp:
        offs.append(o)
        o += len(c)
    nt['platforms'] = [PLATFORM_METAL]
    nt['offsets'] = [offs]
    nt['compressedLengths'] = [[len(c) for c in comp]]
    nt['decompressedLengths'] = [[len(c) for c in raw]]
    nt['stageCounts'] = [tt['stageCounts'][pi]] if tt.get('stageCounts') else []
    nt['compressedBlob'] = b''.join(comp)
    log('  [%s] variants ok=%d fail=%d dropped(non vs/ps)=%d' % (name, stats['ok'], stats['fail'], stats['dropped']))
    return nt, stats

"""Reader/writer for Unity 6000.6 shader blobs (decompressed per-platform blob)."""
import struct, io

class R:
    def __init__(s, b): s.b=b; s.p=0
    def i(s): v=struct.unpack_from('<i',s.b,s.p)[0]; s.p+=4; return v
    def u(s): v=struct.unpack_from('<I',s.b,s.p)[0]; s.p+=4; return v
    def align(s): s.p=(s.p+3)&~3
    def str(s):
        n=s.i(); v=s.b[s.p:s.p+n].decode('utf8','replace'); s.p+=n; s.align(); return v
    def bytes(s,n): v=s.b[s.p:s.p+n]; s.p+=n; return v
class W:
    def __init__(s): s.o=io.BytesIO()
    def i(s,v): s.o.write(struct.pack('<i',v))
    def u(s,v): s.o.write(struct.pack('<I',v&0xffffffff))
    def align(s):
        while s.o.tell()%4: s.o.write(b'\0')
    def str(s,v):
        b=v.encode('utf8'); s.i(len(b)); s.o.write(b); s.align()
    def raw(s,b): s.o.write(b)
    def get(s): return s.o.getvalue()

def read_entries(blob):
    n=struct.unpack_from('<i',blob,0)[0]
    return [struct.unpack_from('<iii',blob,4+12*k) for k in range(n)]

def split_blob(blob):
    return [blob[o:o+l] for (o,l,s) in read_entries(blob)]

def join_blob(segs):
    hdr=4+12*len(segs); out=bytearray(struct.pack('<i',len(segs))); body=bytearray(); offs=[]
    for s in segs:
        offs.append((hdr+len(body),len(s))); body+=s
    for o,l in offs: out+=struct.pack('<iii',o,l,0)
    return bytes(out+body)

# ---------- params (Unity 6000.6, blob version 0x0C1382EA) ----------
# The layout follows SerializedProgramParameters:
#   version, n cbs, cb[ name, size, n vec, vec[name, dim, arraySize, offset], n mat, mat[name, rows, arraySize, offset],
#                       n struct, struct[...] ],
#   n tex, tex[name, enc, slot, samplerEnc, samplerSlot, (dim << 1) | multisampled],
#   n buf, buf[name, enc, slot, arraySize], n cbbind, cbbind[name, enc, slot], n uav, uav[name, enc, slot, orig],
#   n sampler, sampler[enc, slot, flags]
# (buffer / uav / sampler / struct records were not present in the DX11 data; their layout is a guess)
def read_cb(r):
    cb=dict(name=r.str(), size=r.i(), params=[], structs=[])
    for _ in range(r.i()):
        n=r.str(); cols=r.i(); arr=r.i(); off=r.i()
        cb['params'].append(dict(name=n, type=0, rows=1, cols=cols, isMatrix=0, arraySize=arr, index=off))
    for _ in range(r.i()):
        n=r.str(); rows=r.i(); arr=r.i(); off=r.i()
        cb['params'].append(dict(name=n, type=0, rows=rows, cols=rows, isMatrix=1, arraySize=arr, index=off))
    ns=r.i()
    if ns: raise NotImplementedError('struct params in blob')
    return cb
def write_cb(w,cb):
    w.str(cb['name']); w.i(cb['size'])
    vec=[p for p in cb['params'] if not p['isMatrix']]; mat=[p for p in cb['params'] if p['isMatrix']]
    w.i(len(vec))
    for p in vec: w.str(p['name']); w.i(p['cols']); w.i(p['arraySize']); w.i(p['index'])
    w.i(len(mat))
    for p in mat: w.str(p['name']); w.i(p['rows']); w.i(p['arraySize']); w.i(p['index'])
    if cb.get('structs'): raise NotImplementedError('struct params')
    w.i(0)

def read_params(seg):
    r=R(seg); P={'version':r.i()}
    P['cbs']=[read_cb(r) for _ in range(r.i())]
    binds=[]
    for _ in range(r.i()):
        n=r.str(); enc=r.i(); slot=r.i(); senc=r.i(); sslot=r.i(); extra=r.u()
        binds.append(dict(kind='tex',name=n,index=slot,sampler=sslot,extra=extra,enc=enc,senc=senc))
    for _ in range(r.i()):
        n=r.str(); enc=r.i(); slot=r.i(); arr=r.i()
        binds.append(dict(kind='buf',name=n,index=slot,arraySize=arr,enc=enc))
    for _ in range(r.i()):
        n=r.str(); enc=r.i(); slot=r.i()
        binds.append(dict(kind='cb',name=n,index=slot,arraySize=1,enc=enc))
    for _ in range(r.i()):
        n=r.str(); enc=r.i(); slot=r.i(); orig=r.i()
        binds.append(dict(kind='uav',name=n,index=slot,orig=orig,enc=enc))
    for _ in range(r.i()):
        enc=r.i(); slot=r.i(); fl=r.u()
        binds.append(dict(kind='sampler',name='',bind=slot,sampler=fl,enc=enc))
    P['binds']=binds
    P['tail']=seg[r.p:]
    if P['tail']: raise ValueError('param blob: %d bytes left' % len(P['tail']))
    return P
def write_params(P):
    w=W(); w.i(P['version']); w.i(len(P['cbs']))
    for cb in P['cbs']: write_cb(w,cb)
    bk=lambda k:[b for b in P['binds'] if b['kind']==k]
    L=bk('tex'); w.i(len(L))
    for b in L: w.str(b['name']); w.i(b.get('enc',0)); w.i(b['index']); w.i(b.get('senc',0)); w.i(b['sampler']); w.u(b['extra'])
    L=bk('buf'); w.i(len(L))
    for b in L: w.str(b['name']); w.i(b.get('enc',0)); w.i(b['index']); w.i(b['arraySize'])
    L=bk('cb'); w.i(len(L))
    for b in L: w.str(b['name']); w.i(b.get('enc',0)); w.i(b['index'])
    L=bk('uav'); w.i(len(L))
    for b in L: w.str(b['name']); w.i(b.get('enc',0)); w.i(b['index']); w.i(b['orig'])
    L=bk('sampler'); w.i(len(L))
    for b in L: w.i(b.get('enc',0)); w.i(b['bind']); w.u(b['sampler'])
    return w.get()

# ---------- subprogram ----------
def read_sub(seg):
    r=R(seg); S={'version':r.i(),'type':r.i(),'stats':[r.i() for _ in range(4)]}
    S['keywords']=[r.str() for _ in range(r.i())]
    n=r.i(); S['code']=r.bytes(n); r.align()
    S['sourceMap']=r.i(); S['channels']=[(r.i(),r.i()) for _ in range(r.i())]
    S['tail']=seg[r.p:]
    return S
def write_sub(S):
    w=W(); w.i(S['version']); w.i(S['type'])
    for x in S['stats']: w.i(x)
    w.i(len(S['keywords']))
    for k in S['keywords']: w.str(k)
    w.i(len(S['code'])); w.raw(S['code']); w.align()
    w.i(S['sourceMap']); w.i(len(S['channels']))
    for a,b in S['channels']: w.i(a); w.i(b)
    w.raw(S.get('tail',b''))
    return w.get()

def is_params(seg):
    return struct.unpack_from('<i',seg,4)[0] < 4 and False

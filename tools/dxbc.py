import UnityPy, os, sys, json, struct, lz4.block
env = UnityPy.load(sys.argv[1]); out = sys.argv[2]; os.makedirs(out, exist_ok=True)
meta = {}
for o in env.objects:
    if o.type.name != "Shader": continue
    sh = o.read(); pf = sh.m_ParsedForm; name = pf.m_Name
    if not name.startswith("Greymeat/"): continue
    safe = name.replace("/", "_")
    # decompress blobs: one per platform
    plats = list(sh.platforms)
    blobs = []
    raw = bytes(sh.compressedBlob)
    for i, p in enumerate(plats):
        offs = sh.offsets[i]; clen = sh.compressedLengths[i]; dlen = sh.decompressedLengths[i]
        # each may be a list (segments) in newer unity
        if not isinstance(offs, list): offs, clen, dlen = [offs], [clen], [dlen]
        segs = [lz4.block.decompress(raw[a:a+b], uncompressed_size=c) for a, b, c in zip(offs, clen, dlen)]
        blobs.append(segs)
    # unity blob: int count, then (offset,length) pairs, entries are serialized subprograms
    progs = {}
    for pi, segs in enumerate(blobs):
        b = segs[0]
        n = struct.unpack_from("<i", b, 0)[0]
        for k in range(n):
            off, ln = struct.unpack_from("<ii", b, 4 + k * 8)
            ent = b[off:off+ln]
            j = ent.find(b"DXBC")
            if j < 0: continue
            sz = struct.unpack_from("<I", ent, j + 24)[0]
            fn = f"{safe}_p{plats[pi]}_{k}.dxbc"
            open(os.path.join(out, fn), "wb").write(ent[j:j+sz])
            progs[k] = fn
    info = {"props": [], "subs": []}
    for p in pf.m_PropInfo.m_Props:
        info["props"].append({"name": p.m_Name, "desc": p.m_Description, "type": int(p.m_Type), "def": list(p.m_DefValue) if hasattr(p, "m_DefValue") else None,
                              "tex": getattr(getattr(p, "m_DefTexture", None), "m_DefaultName", None), "flags": int(getattr(p, "m_Flags", 0))})
    for ss in pf.m_SubShaders:
        S = {"tags": dict(ss.m_Tags.tags) if hasattr(ss.m_Tags, "tags") else {}, "lod": ss.m_LOD, "passes": []}
        for ps in ss.m_Passes:
            st = ps.m_State
            def fv(x):
                try: return x.val
                except Exception: return str(x)
            rb = st.rtBlend[0] if hasattr(st, "rtBlend") else None
            P = {"type": int(ps.m_Type), "tags": dict(ps.m_Tags.tags) if hasattr(ps.m_Tags, "tags") else {}, "name": st.m_Name,
                 "zwrite": fv(st.zWrite), "ztest": fv(st.zTest), "cull": fv(st.culling), "offset": [fv(st.offsetFactor), fv(st.offsetUnits)],
                 "blend": [fv(rb.srcBlend), fv(rb.destBlend), fv(rb.srcBlendAlpha), fv(rb.destBlendAlpha), fv(rb.colMask)] if rb else None,
                 "stencil": [fv(st.stencilRef), fv(st.stencilReadMask), fv(st.stencilWriteMask)] if hasattr(st, "stencilRef") else None,
                 "progs": {}}
            for stage in ["m_ProgVertex", "m_ProgFragment", "m_ProgGeometry", "m_ProgHull", "m_ProgDomain"]:
                pr = getattr(ps, stage, None) or getattr(ps, stage[2].lower()+stage[3:], None)
                if pr is None: continue
                L = []
                for sp in pr.m_SubPrograms:
                    kw = []
                    for attr in ["m_GlobalKeywordIndices", "m_LocalKeywordIndices", "m_KeywordIndices"]:
                        if hasattr(sp, attr): kw += list(getattr(sp, attr))
                    L.append({"blob": sp.m_BlobIndex, "gpu": int(sp.m_GpuProgramType), "kw": kw, "file": progs.get(sp.m_BlobIndex)})
                if L: P["progs"][stage] = L
            # keyword names
            S["passes"].append(P)
        info["subs"].append(S)
    kwn = {}
    try:
        for i, n in enumerate(pf.m_KeywordNames): kwn[i] = n
    except Exception: pass
    info["keywords"] = kwn
    meta[name] = info
json.dump(meta, open(os.path.join(out, "shaders.json"), "w"), indent=1, default=str)
for k, v in meta.items():
    print(k, len(v["subs"]), [(p["name"], {s: len(l) for s, l in p["progs"].items()}) for s in v["subs"] for p in s["passes"]])

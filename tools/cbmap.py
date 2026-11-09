import UnityPy, sys, json
env = UnityPy.load(sys.argv[1]); out = {}
def names(ps):
    ni = ps.m_NameIndices; inv = {}
    for e in ni:
        if isinstance(e, (list, tuple)): inv[int(e[1])] = e[0]
    return inv
def params(p, inv):
    r = {"cbs": [], "binds": [], "tex": [], "smp": [], "vec": [], "mat": []}
    for cb in p.m_ConstantBuffers:
        r["cbs"].append({"name": inv.get(cb.m_NameIndex), "size": cb.m_Size,
            "vec": [[inv.get(v.m_NameIndex), v.m_OffsetInConstantBuffer, v.m_Dim, v.m_ArraySize, v.m_Type] for v in cb.m_VectorParams],
            "mat": [[inv.get(v.m_NameIndex), v.m_OffsetInConstantBuffer, getattr(v,"m_RowCount",4), getattr(v,"m_ColumnCount",4), v.m_ArraySize] for v in cb.m_MatrixParams]})
    for b in p.m_ConstantBufferBindings:
        r["binds"].append([inv.get(b.m_NameIndex), getattr(getattr(b,"m_Binding",None),"m_Slot",getattr(b,"m_Index",None))])
    for t in p.m_TextureParams:
        r["tex"].append([inv.get(t.m_NameIndex), getattr(t,"m_Index",None), getattr(t,"m_SamplerIndex",None), getattr(t,"m_Dim",None)])
    for s in getattr(p, "m_Samplers", []) or []:
        r["smp"].append([s.sampler, s.bindPoint] if hasattr(s, "sampler") else str(s))
    return r
for o in env.objects:
    if o.type.name != "Shader": continue
    sh = o.read(); pf = sh.m_ParsedForm
    if not pf.m_Name.startswith("Greymeat/"): continue
    kw = list(getattr(pf, "m_KeywordNames", []) or [])
    S = {"keywords": kw, "props": [[p.m_Name, p.m_Description, int(p.m_Type), [getattr(p,f"m_DefValue_{i}_",0) for i in range(4)], getattr(p.m_DefTexture, "m_DefaultName", "")] for p in pf.m_PropInfo.m_Props], "passes": [], "subtags": {}}
    ss = pf.m_SubShaders[0]
    try: S["subtags"] = dict(ss.m_Tags.tags)
    except Exception: pass
    for ps in ss.m_Passes:
        inv = names(ps); st = ps.m_State
        def fv(x):
            try: return x.val
            except Exception: return x
        P = {"type": int(ps.m_Type), "tags": {}, "state": {}}
        try: P["tags"] = dict(st.m_Tags.tags)
        except Exception: pass
        rb = st.rtBlend[0] if hasattr(st, "rtBlend") else (st.rtBlend0 if hasattr(st, "rtBlend0") else None)
        P["state"] = {"zwrite": fv(st.zWrite), "ztest": fv(st.zTest), "cull": fv(st.culling), "ofs": [fv(st.offsetFactor), fv(st.offsetUnits)],
                      "blend": [fv(rb.srcBlend), fv(rb.destBlend), fv(rb.srcBlendAlpha), fv(rb.destBlendAlpha), fv(rb.blendOp), fv(rb.colMask)] if rb else None,
                      "lighting": getattr(st, "lighting", None)}
        for stage in ["progVertex", "progFragment"]:
            pr = getattr(ps, stage)
            L = []
            pbi = pr.m_ParameterBlobIndices
            common = params(pr.m_CommonParameters, inv) if hasattr(pr, "m_CommonParameters") else None
            for gi, lst in enumerate(pr.m_PlayerSubPrograms):
                for sp in lst:
                    L.append({"blob": sp.m_BlobIndex, "gpu": int(sp.m_GpuProgramType), "kw": [kw[i] if i < len(kw) else i for i in sp.m_KeywordIndices], "plat": gi})
            P[stage] = {"subs": L, "common": common, "pbi": [list(x) for x in pbi]}
        S["passes"].append(P)
    out[pf.m_Name] = S
json.dump(out, open("meta.json", "w"), indent=1, default=str)
for k, v in out.items():
    print(k, v["subtags"], [(p["tags"], len(p["progVertex"]["subs"]), sorted({tuple(s["kw"]) for s in p["progFragment"]["subs"]})[:6]) for p in v["passes"]])

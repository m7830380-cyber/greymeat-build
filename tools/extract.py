import UnityPy, os, sys, json
src, out = sys.argv[1], sys.argv[2]
env = UnityPy.load(src)
R = os.path.join(out, "Resources")
info = {"shaders": {}, "container": {}, "models": {}}
os.makedirs(R, exist_ok=True)

def mesh_obj(m):
    try:
        return m.export()
    except Exception as e:
        print("mesh fail", m.m_Name, e); return None

def go_info(go):
    # flatten a prefab: [(name, path, localpos, localrot, localscale, meshname, materials)]
    parts = []
    def walk(t, path):
        g = t.m_GameObject.read()
        mesh = None; mats = []
        for c in g.m_Component:
            cc = c.component if hasattr(c, "component") else c[1]
            try: o = cc.read()
            except Exception: continue
            tn = o.object_reader.type.name if hasattr(o, "object_reader") else type(o).__name__
            if tn == "MeshFilter" and o.m_Mesh:
                try: mesh = o.m_Mesh.read().m_Name
                except Exception: pass
            if tn == "MeshRenderer":
                for mm in o.m_Materials:
                    try: mats.append(mm.read().m_Name)
                    except Exception: mats.append("")
        lp, lr, ls = t.m_LocalPosition, t.m_LocalRotation, t.m_LocalScale
        parts.append({"name": g.m_Name, "path": path, "p": [lp.x, lp.y, lp.z], "r": [lr.x, lr.y, lr.z, lr.w], "s": [ls.x, ls.y, ls.z], "mesh": mesh, "mats": mats})
        for ch in t.m_Children:
            ct = ch.read()
            walk(ct, path + "/" + ct.m_GameObject.read().m_Name)
    tr=None
    for c in go.m_Component:
        cc = c.component if hasattr(c, "component") else c[1]
        try:
            o=cc.read()
            if o.object_reader.type.name=="Transform": tr=o;break
        except Exception: pass
    walk(tr, "")
    return parts

meshes_done = set()
import struct
def rm_container():
    out={}
    for o in env.objects:
        if o.type.name!="ResourceManager": continue
        b=o.get_raw_data(); f=o.assets_file; pos=0
        def i32():
            nonlocal pos; v=struct.unpack_from("<i",b,pos)[0]; pos+=4; return v
        n=i32()
        for _ in range(n):
            l=i32(); name=b[pos:pos+l].decode("utf-8","replace"); pos+=l; pos=(pos+3)&~3
            fid=i32(); pid=struct.unpack_from("<q",b,pos)[0]; pos+=8
            out[name]=(fid,pid)
    return out
rmc=rm_container(); print("rm entries",len(rmc),list(rmc)[:20])
byid={}
for o in env.objects: byid.setdefault(o.path_id,[]).append(o)
cont={}
for name,(fid,pid) in rmc.items():
    pr=["GameObject","Font","AudioClip","TextAsset","Shader","Texture2D","Mesh"]
    c=sorted(byid.get(pid,[]),key=lambda o:pr.index(o.type.name) if o.type.name in pr else 99)
    if name.startswith("models/") and not name.endswith("_physics"):
        b=name.split("/")[-1]
        gos=[o for o in env.objects if o.type.name=="GameObject" and o.read().m_Name.lower()==b]
        if gos: c=gos
    if c: cont["assets/resources/"+name]=c[0]
for path, obj in cont.items():
    p = path.lower()
    path = path
    p = path[len("assets/resources/"):] if p.startswith("assets/resources/") else path
    base, ext = os.path.splitext(p)
    t = obj.type.name
    info["container"][p] = t
    try:
        d = obj.read()
    except Exception as e:
        print("read fail", p, e); continue
    dst = os.path.join(R, os.path.dirname(p)); os.makedirs(dst, exist_ok=True)
    name = os.path.basename(base)
    # Resources paths are case-sensitive on iOS; use original asset name casing
    real = getattr(d, "m_Name", name) or name
    try:
        if t == "Texture2D":
            d.image.save(os.path.join(dst, real + ".png"))
        elif t == "AudioClip":
            for n, data in d.samples.items():
                open(os.path.join(dst, real + os.path.splitext(n)[1]), "wb").write(data); break
        elif t == "Font":
            open(os.path.join(dst, real + ".ttf"), "wb").write(bytes(d.m_FontData))
        elif t == "TextAsset":
            raw = d.m_Script
            if isinstance(raw, str): raw = raw.encode("utf-8", "surrogateescape")
            open(os.path.join(dst, real + ".bytes"), "wb").write(raw)
        elif t == "GameObject":
            parts = go_info(d)
            info["models"][base] = parts
        elif t == "Shader":
            pass
        else:
            print("skip", p, t)
    except Exception as e:
        print("fail", p, t, e)

os.makedirs(os.path.join(out, "Meshes"), exist_ok=True)
for o in env.objects:
    if o.type.name == "Mesh":
        try:
            m = o.read(); s = mesh_obj(m)
            if s: open(os.path.join(out, "Meshes", m.m_Name + ".obj"), "w").write(s)
        except Exception as e: print("mesh", e)
    if o.type.name == "Shader":
        try:
            sh = o.read()
            pf = sh.m_ParsedForm
            props = [(p.m_Name, str(p.m_Type), p.m_Description) for p in pf.m_PropInfo.m_Props]
            subs = []
            for ss in pf.m_SubShaders:
                tags = {k: v for k, v in ss.m_Tags.tags} if hasattr(ss.m_Tags, "tags") else str(ss.m_Tags)
                passes = []
                for ps in ss.m_Passes:
                    st = ps.m_State
                    passes.append({"name": getattr(st, "m_Name", ""), "tags": str(getattr(ps, "m_Tags", "")),
                                   "zwrite": str(getattr(st, "zWrite", "")), "cull": str(getattr(st, "culling", "")),
                                   "blend": str(getattr(st, "rtBlend", ["?"])[0] if hasattr(st, "rtBlend") else "")[:400]})
                subs.append({"tags": str(tags), "passes": passes})
            info["shaders"][pf.m_Name] = {"props": props, "subs": subs}
        except Exception as e:
            print("shader", e)
json.dump(info, open(os.path.join(out, "info.json"), "w"), indent=1, default=str)
print("containers", len(info["container"]), "shaders", list(info["shaders"].keys()))

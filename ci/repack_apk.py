"""Swap converted game data into the Unity shell APK.
  python ci/repack_apk.py <shell.apk> <data_dir> <retail RuntimeInitializeOnLoads.json> <out_unsigned.apk>
Keeps every shell entry (except old signatures and the shell's own level/sharedassets/globalgamemanagers/resources
data), merges RuntimeInitializeOnLoads.json and stores the game files uncompressed (Unity memory-maps them)."""
import json, os, re, sys, zipfile
apk, data, rt_json, out = sys.argv[1:5]
D = 'assets/bin/Data/'
DROP = re.compile(r'^(META-INF/.*\.(SF|RSA|EC|DSA)|META-INF/MANIFEST\.MF|' + re.escape(D) +
                  r'(level\d+.*|sharedassets\d+.*|globalgamemanagers.*|resources\.(assets|resource).*|Resources/unity_builtin_extra.*))$')
new = {}
for root, _, files in os.walk(data):
    for f in files:
        p = os.path.join(root, f)
        new[D + os.path.relpath(p, data).replace(os.sep, '/')] = p


def android_player_settings(shell_ggm, game_ggm_path):
    """The game's globalgamemanagers was built for Windows: BuildSettings.m_GraphicsAPIs is [D3D12, D3D11], so the
    Android player finds no usable graphics API ("Unable to initialize the Unity Engine Graphics API"), and
    PlayerSettings has the Windows values (playerMinOpenGLESVersion 0, mobile MT rendering/GPU skinning off).
    Take the shell's Android graphics API list and PlayerSettings blob (same Unity version, same layout)."""
    import UnityPy
    UnityPy.config.FALLBACK_UNITY_VERSION = '6000.6.0f1'
    obj = lambda env, t: next(o for o in env.objects if o.type.name == t)
    shell = UnityPy.load(shell_ggm)
    env = UnityPy.load(game_ggm_path)
    bs = obj(env, 'BuildSettings')
    t = bs.read_typetree(check_read=False)
    apis = obj(shell, 'BuildSettings').read_typetree(check_read=False)['m_GraphicsAPIs']
    print('graphics APIs', t['m_GraphicsAPIs'], '->', apis)
    t['m_GraphicsAPIs'] = apis
    bs.save_typetree(t)
    ps, raw = obj(env, 'PlayerSettings'), obj(shell, 'PlayerSettings').get_raw_data()
    if len(ps.get_raw_data()) == len(raw):
        ps.set_raw_data(raw)
        print('PlayerSettings: using the shell (Android) copy')
    else:
        print('PlayerSettings size differs, keeping game copy')
    return list(env.files.values())[0].save()


with zipfile.ZipFile(apk) as zi, zipfile.ZipFile(out, 'w') as zo:
    names = zi.namelist()
    if D + 'globalgamemanagers' in names and D + 'globalgamemanagers' in new:
        b = android_player_settings(zi.read(D + 'globalgamemanagers'), new[D + 'globalgamemanagers'])
        if b:
            fixed = out + '.ggm'
            open(fixed, 'wb').write(b)
            new[D + 'globalgamemanagers'] = fixed
    print('shell data:', sorted(n[len(D):] for n in names if n.startswith(D)))
    for info in zi.infolist():
        n = info.filename
        if DROP.match(n) or n in new:
            continue
        b = zi.read(n)
        if n == D + 'RuntimeInitializeOnLoads.json':
            sh = json.loads(b); rt = json.load(open(rt_json))
            k = lambda e: (e['assemblyName'], e['className'], e['methodName'])
            seen = {k(e) for e in sh['root']}
            sh['root'] += [e for e in rt['root'] if k(e) not in seen]
            b = json.dumps(sh).encode()
            print('init:', [e['className'] + '.' + e['methodName'] for e in sh['root']])
        z = zipfile.ZipInfo(n, info.date_time)
        z.compress_type = zipfile.ZIP_STORED if n.startswith('assets/aot/') else info.compress_type
        z.external_attr = info.external_attr
        zo.writestr(z, b)
    for n, p in sorted(new.items()):
        zo.write(p, n, compress_type=zipfile.ZIP_STORED)
print('wrote', out, os.path.getsize(out))

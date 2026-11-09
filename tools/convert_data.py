"""Convert the Greymeat Windows player data for the iOS player.

  python tools/convert_data.py <Greymeat_Data> <out_dir>

For every serialized file (globalgamemanagers(.assets), level0, sharedassets0, resources.assets, unity_builtin_extra):
  * target platform StandaloneWindows64 (19) -> iOS (9)
  * Shader DX11 programs -> Metal (HLSLcc, convert_shader.py)
  * BCn textures -> ASTC (transcode_tex.py), .resS rebuilt
Audio (resources.resource) is copied unchanged. "unity default resources" is NOT converted: the iOS shell has its own.
Writes the files into out_dir (relative to the app's Data folder) and report.json.
"""
import json, os, shutil, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import UnityPy
import convert_shader
from convert_shader import convert_shader as convert_one
import gles
from transcode_tex import transcode_file

UnityPy.config.FALLBACK_UNITY_VERSION = '6000.6.0f1'
IOS_PLATFORM = 9
ANDROID_PLATFORM = 13
ANDROID = os.environ.get('GREY_TARGET') == 'android'   # GLES3 shaders, Android serialized files
# GREY_TEX=keep: leave the desktop BC1-7 textures as they are (Android emulators such as BlueStacks run on a PC GPU
# with S3TC/RGTC/BPTC, and their software ASTC 6x6 decoder draws noise). Phones need the ASTC default.
KEEP_TEX = os.environ.get('GREY_TEX') == 'keep'
FILES = ['globalgamemanagers', 'globalgamemanagers.assets', 'level0', 'sharedassets0.assets', 'resources.assets',
         'Resources/unity_builtin_extra']
COPY = ['resources.resource']


def convert_shaders_in(sf, work, report):
    n = 0
    for obj in sf.objects.values():
        if obj.type.name != 'Shader':
            continue
        tt = obj.read_typetree()
        blob = bytes(tt['compressedBlob'])
        name = tt['m_ParsedForm']['m_Name']
        msgs = []
        tt['compressedBlob'] = b''
        try:
            res = (gles.convert_shader if ANDROID else convert_one)(tt, blob, os.path.join(work, 'hlslcc', str(obj.path_id)), log=msgs.append)
        except Exception:
            report.setdefault('shader_errors', {})[name] = traceback.format_exc()
            continue
        if res is None:
            report.setdefault('no_dx11', []).append(name)
            continue
        nt, stats = res
        report.setdefault('shaders', {})[name] = stats
        if stats.get('fail'):
            report.setdefault('shader_msgs', {})[name] = msgs[:40]
        obj.save_typetree(nt)
        n += 1
    return n


def main():
    src, out = sys.argv[1], sys.argv[2]
    work = os.path.join(out + '_work')
    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(out, 'Resources'), exist_ok=True)
    report = {}
    for f in FILES:
        t0 = time.time()
        rep = report.setdefault(f, {})
        try:
            env = UnityPy.load(os.path.join(src, f))
            sf = list(env.files.values())[0]
            rep['shaders_converted'] = convert_shaders_in(sf, work, rep)
            rep['textures_converted'] = 'kept' if KEEP_TEX else transcode_file(sf, src, out, rep)
            sf._m_target_platform = ANDROID_PLATFORM if ANDROID else IOS_PLATFORM
            with open(os.path.join(out, f), 'wb') as fp:
                fp.write(sf.save())
            res = f + '.resS'
            if os.path.exists(os.path.join(src, res)) and res not in rep.get('rebuilt_streams', []):
                shutil.copy(os.path.join(src, res), os.path.join(out, res))
        except Exception:
            rep['error'] = traceback.format_exc()
        rep['seconds'] = round(time.time() - t0)
        sh = rep.get('shaders', {})
        print('%-32s %3ds shaders %d (fail variants %d) textures %s errors %s %s' % (
            f, rep['seconds'], rep.get('shaders_converted', 0), sum(s.get('fail', 0) for s in sh.values()),
            rep.get('textures_converted'), len(rep.get('texture_errors', [])), 'ERROR' if 'error' in rep else ''), flush=True)
        if 'error' in rep:
            print(rep['error'])
    for f in COPY:
        if os.path.exists(os.path.join(src, f)):
            shutil.copy(os.path.join(src, f), os.path.join(out, f))
    for k, v in convert_shader.INLINE_SEEN.items():
        report.setdefault('_inline', {})[str(k)] = v
    json.dump(report, open(os.path.join(out + '_report.json'), 'w'), indent=1, default=str)
    shutil.rmtree(work, ignore_errors=True)
    bad = [f for f in FILES if 'error' in report[f]]
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()

using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Runtime.CompilerServices;
using UnityEngine;

namespace HybridCLR
{
    // Same names as HybridCLR.Runtime's RuntimeApi so the icall registered by the patched libil2cpp
    // ("HybridCLR.RuntimeApi::LoadMetadataForAOTAssembly(System.Byte[],HybridCLR.HomologousImageMode)") binds here.
    public enum HomologousImageMode { Consistent, SuperSet }

    public static class RuntimeApi
    {
        [MethodImpl(MethodImplOptions.InternalCall)]
        public static extern int LoadMetadataForAOTAssembly(byte[] dllBytes, HomologousImageMode mode);
    }
}

namespace GreyShim
{
    // Mod DLLs run in HybridCLR's interpreter. A generic the game never instantiated (List<KeyValuePair<Light,float>>
    // in a mod) has no AOT code, so the interpreter needs the original IL of those methods: the build ships the
    // AOT assemblies in StreamingAssets/aot (shell HybridSetup), loaded here before any mod runs.
    static class AotMeta
    {
        public static void Load()
        {
            try
            {
                int ok = 0, n = 0;
                foreach (var kv in Files())
                {
                    n++;
                    int err = HybridCLR.RuntimeApi.LoadMetadataForAOTAssembly(kv.Value, HybridCLR.HomologousImageMode.SuperSet);
                    if (err == 0) ok++; else Debug.LogWarning($"[Greymeat] AOT metadata {kv.Key}: error {err}");
                }
                Debug.Log($"[Greymeat] AOT metadata loaded {ok}/{n}");
            }
            catch (Exception e) { Debug.LogWarning("[Greymeat] AOT metadata not loaded: " + e.GetType().Name + " " + e.Message); }
        }

        static IEnumerable<KeyValuePair<string, byte[]>> Files()
        {
            if (Application.platform != RuntimePlatform.Android)
            {
                string dir = Path.Combine(Application.streamingAssetsPath, "aot");
                if (!Directory.Exists(dir)) yield break;
                foreach (var f in Directory.GetFiles(dir, "*.bytes"))
                    yield return new KeyValuePair<string, byte[]>(Path.GetFileName(f), File.ReadAllBytes(f));
                yield break;
            }
            // Android: StreamingAssets live inside the APK (Application.dataPath); read assets/aot/* from the zip.
            using (var fs = new FileStream(Application.dataPath, FileMode.Open, FileAccess.Read))
            using (var br = new BinaryReader(fs))
            {
                long eocd = -1;
                for (long p = fs.Length - 22; p >= Math.Max(0, fs.Length - 65557); p--)
                {
                    fs.Position = p;
                    if (br.ReadUInt32() == 0x06054b50) { eocd = p; break; }
                }
                if (eocd < 0) yield break;
                fs.Position = eocd + 10;
                int count = br.ReadUInt16();
                br.ReadUInt32();
                long cd = br.ReadUInt32();
                var hits = new List<(string name, int method, long csize, long usize, long off)>();
                fs.Position = cd;
                for (int i = 0; i < count; i++)
                {
                    if (br.ReadUInt32() != 0x02014b50) break;
                    fs.Position += 6;
                    int method = br.ReadUInt16();
                    fs.Position += 8;
                    long csize = br.ReadUInt32(), usize = br.ReadUInt32();
                    int nl = br.ReadUInt16(), el = br.ReadUInt16(), cl = br.ReadUInt16();
                    fs.Position += 8;
                    long off = br.ReadUInt32();
                    string name = System.Text.Encoding.UTF8.GetString(br.ReadBytes(nl));
                    fs.Position += el + cl;
                    if (name.StartsWith("assets/aot/") && name.EndsWith(".bytes")) hits.Add((name, method, csize, usize, off));
                }
                foreach (var h in hits)
                {
                    fs.Position = h.off + 26;
                    int nl = br.ReadUInt16(), el = br.ReadUInt16();
                    fs.Position = h.off + 30 + nl + el;
                    byte[] data = br.ReadBytes((int)h.csize);
                    if (h.method == 8)
                    {
                        var outb = new byte[h.usize];
                        using (var ds = new DeflateStream(new MemoryStream(data), CompressionMode.Decompress))
                        {
                            int got = 0, r;
                            while (got < outb.Length && (r = ds.Read(outb, got, outb.Length - got)) > 0) got += r;
                        }
                        data = outb;
                    }
                    yield return new KeyValuePair<string, byte[]>(Path.GetFileName(h.name), data);
                }
            }
        }
    }
}

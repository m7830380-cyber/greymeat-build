using System;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;

// HybridCLR (IL2CPP + interpreter) lets mod DLLs from the in-game workshop run: on plain IL2CPP,
// Assembly.Load(byte[]) is not supported. CI installs the patched libil2cpp into
// HybridCLRData/LocalIl2CppData-OSXEditor/il2cpp (tools/hybridclr/prepare.sh) before Unity starts;
// this runs HybridCLR's code generation (method bridges for every AOT signature) before the real build.
public static class HybridSetup
{
    public static bool Enabled => Environment.GetEnvironmentVariable("HYBRIDCLR") == "1";

    public static void Prebuild(string scenePath)
    {
        if (!Enabled) { Debug.Log("[HybridSetup] disabled (HYBRIDCLR != 1)"); return; }
        // GenerateStripedAOTDlls builds with EditorBuildSettings' scenes
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(scenePath, true) };
        var asm = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a => a.GetName().Name == "HybridCLR.Editor");
        if (asm == null) throw new Exception("[HybridSetup] HybridCLR.Editor assembly not loaded");
        var settingsType = asm.GetTypes().First(t => t.Name == "HybridCLRSettings");
        var inst = settingsType.GetProperty("Instance", BindingFlags.Public | BindingFlags.Static).GetValue(null);
        settingsType.GetField("enable").SetValue(inst, true);
        settingsType.GetMethod("Save", BindingFlags.Public | BindingFlags.Static).Invoke(null, null);
        var cmd = asm.GetTypes().First(t => t.Name == "PrebuildCommand");
        Debug.Log("[HybridSetup] HybridCLR GenerateAll ...");
        cmd.GetMethod("GenerateAll", BindingFlags.Public | BindingFlags.Static).Invoke(null, null);
        // HybridCLR writes Assets/<outputAOTGenericReferenceFile> (a reference-only MonoBehaviour). Left in
        // Assets it compiles into a new Assembly-CSharp that replaces the game's precompiled one, so the
        // player only had AOTGenericReferences (Bootstrap missing, black screen). Delete it before the build.
        string aot = (string)settingsType.GetField("outputAOTGenericReferenceFile").GetValue(inst);
        foreach (var f in new[] { Path.Combine(Application.dataPath, aot), Path.Combine(Application.dataPath, aot) + ".meta" })
            if (File.Exists(f)) { File.Delete(f); Debug.Log("[HybridSetup] removed " + f); }
        AssetDatabase.Refresh();
        string mb = "HybridCLRData/LocalIl2CppData-" + Application.platform + "/il2cpp/libil2cpp/hybridclr/generated/MethodBridge.cpp";
        Debug.Log("[HybridSetup] GenerateAll done; MethodBridge " + (File.Exists(mb) ? new FileInfo(mb).Length + " bytes" : "MISSING"));
    }
}

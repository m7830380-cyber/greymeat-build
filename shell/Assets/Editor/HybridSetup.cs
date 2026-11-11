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
        string mb = "HybridCLRData/LocalIl2CppData-" + Application.platform + "/il2cpp/libil2cpp/hybridclr/generated/MethodBridge.cpp";
        Debug.Log("[HybridSetup] GenerateAll done; MethodBridge " + (File.Exists(mb) ? new FileInfo(mb).Length + " bytes" : "MISSING"));
    }
}

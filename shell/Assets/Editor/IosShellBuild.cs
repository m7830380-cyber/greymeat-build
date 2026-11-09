#if UNITY_IOS
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Callbacks;
using UnityEditor.iOS.Xcode;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

// Builds the iOS player "shell" with Unity 6000.3.15f1 (the game's exact version): an IL2CPP player that
// contains the patched retail PEAK assemblies (Assets/Plugins/Game, from the private repo), the iOS
// mod and the native helpers (Plugins/iOS/*.mm). The game's own data files are swapped into the .app afterwards
// by tools/package_ipa.py (privately, on the user's PC / private repo).
public static class IosShellBuild
{
    const string ScenePath = "Assets/Scenes/Boot.unity";
    static readonly NamedBuildTarget Target = NamedBuildTarget.iOS;

    public static void Build()
    {
        string outDir = Environment.GetEnvironmentVariable("SHELL_OUT") ?? "Build/iOS";

        if (!File.Exists(ScenePath))
        {
            Directory.CreateDirectory("Assets/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            EditorSceneManager.SaveScene(scene, ScenePath);
        }

        EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.iOS, BuildTarget.iOS);

        PlayerSettings.companyName = "Greymeat";
        PlayerSettings.productName = "Greymeat";
        PlayerSettings.SetApplicationIdentifier(Target, "com.greymeat.iosport");
        PlayerSettings.bundleVersion = "1.3.4";
        PlayerSettings.iOS.buildNumber = Environment.GetEnvironmentVariable("GITHUB_RUN_NUMBER") ?? "1";
        PlayerSettings.SetScriptingBackend(Target, ScriptingImplementation.IL2CPP);
        // link.xml keeps every game assembly whole; Minimal is the least stripping IL2CPP allows.
        PlayerSettings.SetManagedStrippingLevel(Target, ManagedStrippingLevel.Minimal);
        PlayerSettings.stripEngineCode = false;
        PlayerSettings.SetIl2CppCompilerConfiguration(Target, Il2CppCompilerConfiguration.Release);
        PlayerSettings.SetApiCompatibilityLevel(Target, ApiCompatibilityLevel.NET_Unity_4_8);
        PlayerSettings.allowUnsafeCode = true;
        PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.iOS, false);
        PlayerSettings.SetGraphicsAPIs(BuildTarget.iOS, new[] { GraphicsDeviceType.Metal });
        PlayerSettings.colorSpace = Environment.GetEnvironmentVariable("COLOR_SPACE") == "Gamma" ? ColorSpace.Gamma : ColorSpace.Linear;
        PlayerSettings.gpuSkinning = true;                  // same as the retail game
        PlayerSettings.MTRendering = true;
        PlayerSettings.iOS.targetOSVersionString = "15.0";
        PlayerSettings.iOS.sdkVersion = iOSSdkVersion.DeviceSDK;
        PlayerSettings.iOS.targetDevice = iOSTargetDevice.iPhoneAndiPad;
        PlayerSettings.iOS.requiresFullScreen = true;
        PlayerSettings.iOS.hideHomeButton = true;
        PlayerSettings.iOS.deferSystemGesturesMode = UnityEngine.iOS.SystemGestureDeferMode.All;
        
        PlayerSettings.iOS.appInBackgroundBehavior = iOSAppInBackgroundBehavior.Suspend;
        PlayerSettings.statusBarHidden = true;
        PlayerSettings.accelerometerFrequency = 0;
        PlayerSettings.defaultInterfaceOrientation = UIOrientation.AutoRotation;
        PlayerSettings.allowedAutorotateToLandscapeLeft = true;
        PlayerSettings.allowedAutorotateToLandscapeRight = true;
        PlayerSettings.allowedAutorotateToPortrait = false;
        PlayerSettings.allowedAutorotateToPortraitUpsideDown = false;
        PlayerSettings.SplashScreen.show = false;
        PlayerSettings.SplashScreen.showUnityLogo = false;
        PlayerSettings.enableInternalProfiler = false;
        PlayerSettings.SetStackTraceLogType(LogType.Log, StackTraceLogType.None);
        PlayerSettings.SetStackTraceLogType(LogType.Warning, StackTraceLogType.None);
        PlayerSettings.SetStackTraceLogType(LogType.Error, StackTraceLogType.ScriptOnly);
        PlayerSettings.SetStackTraceLogType(LogType.Assert, StackTraceLogType.ScriptOnly);
        PlayerSettings.SetStackTraceLogType(LogType.Exception, StackTraceLogType.ScriptOnly);
        PlayerSettings.usePlayerLog = true;
        PlayerSettings.iOS.appleEnableAutomaticSigning = false;
        SetProjectSetting("activeInputHandler", 0);         // legacy Input Manager only, like the retail game
        DisableThermalFps();
        // The retail game's PlayerSettings (in its globalgamemanagers) replace most of these at runtime,
        // but the shell must agree on anything baked into the player binary.

        var plugins = Directory.Exists("Assets/Plugins/Game") ? Directory.GetFiles("Assets/Plugins/Game", "*.dll").Length : 0;
        Debug.Log($"[IosShellBuild] {plugins} game assemblies");
        if (plugins == 0) Debug.LogWarning("[IosShellBuild] no game assemblies in Assets/Plugins/Game - building an empty shell");

        var opts = new BuildPlayerOptions
        {
            scenes = new[] { ScenePath },
            locationPathName = outDir,
            target = BuildTarget.iOS,
            targetGroup = BuildTargetGroup.iOS,
            options = BuildOptions.None,
        };
        var report = BuildPipeline.BuildPlayer(opts);
        Debug.Log("[IosShellBuild] result: " + report.summary.result + " errors=" + report.summary.totalErrors);
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
        {
            // Keep the linked assemblies IL2CPP choked on (Library/Temp is wiped when the editor quits) so CI
            // can hand them to the PRIVATE repo for inspection. They contain game code: never publish them publicly.
            string dbg = Environment.GetEnvironmentVariable("SHELL_DEBUG_OUT");
            foreach (var staged in new[] { "Library/Bee/artifacts/iOS/ManagedStripped", "Temp/StagingArea/Data/Managed" })
            {
                if (string.IsNullOrEmpty(dbg) || !Directory.Exists(staged)) continue;
                Directory.CreateDirectory(dbg);
                foreach (var f in Directory.GetFiles(staged, "*.dll"))
                    File.Copy(f, Path.Combine(dbg, Path.GetFileName(f)), true);
                Debug.Log("[IosShellBuild] copied staged assemblies from " + staged + " to " + dbg);
            }
            EditorApplication.Exit(1);
        }
    }

    // "Adjust iOS FPS based on thermal state" drops the target to 30 as soon as the phone reports Serious
    // (which a warm iPhone does almost immediately). The API / field name differs between Unity versions,
    // so switch off everything thermal-related found by name, in both the API and the serialized asset.
    static void DisableThermalFps()
    {
        foreach (var t in new[] { typeof(PlayerSettings.iOS), typeof(PlayerSettings) })
            foreach (var pi in t.GetProperties(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static))
            {
                if (pi.Name.IndexOf("hermal", StringComparison.OrdinalIgnoreCase) < 0) continue;
                if (pi.PropertyType == typeof(bool) && pi.CanWrite) { pi.SetValue(null, false); Debug.Log($"[IosShellBuild] {t.Name}.{pi.Name} = false"); }
                else Debug.Log($"[IosShellBuild] thermal property {t.Name}.{pi.Name} ({pi.PropertyType.Name}) left alone");
            }
        var asset = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset").FirstOrDefault();
        if (asset == null) return;
        var so = new SerializedObject(asset);
        var it = so.GetIterator();
        bool any = false;
        while (it.Next(true))
        {
            if (it.name.IndexOf("hermal", StringComparison.OrdinalIgnoreCase) < 0) continue;
            any = true;
            if (it.propertyType == SerializedPropertyType.Boolean) it.boolValue = false;
            else if (it.propertyType == SerializedPropertyType.Integer) it.intValue = 0;
            Debug.Log($"[IosShellBuild] project setting {it.propertyPath} ({it.propertyType}) -> off");
        }
        if (!any) Debug.LogWarning("[IosShellBuild] no thermal project setting found");
        so.ApplyModifiedPropertiesWithoutUndo();
    }

    static void SetProjectSetting(string name, int value)
    {
        var asset = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset").FirstOrDefault();
        if (asset == null) { Debug.LogWarning("[IosShellBuild] ProjectSettings.asset not found"); return; }
        var so = new SerializedObject(asset);
        var p = so.FindProperty(name);
        if (p == null) { Debug.LogWarning("[IosShellBuild] project setting missing: " + name); return; }
        p.intValue = value;
        so.ApplyModifiedPropertiesWithoutUndo();
        Debug.Log($"[IosShellBuild] {name} = {value}");
    }

    [PostProcessBuild(100)]
    public static void OnPostprocess(BuildTarget target, string path)
    {
        if (target != BuildTarget.iOS) return;

        // ---- Info.plist ----
        string plistPath = Path.Combine(path, "Info.plist");
        var plist = new PlistDocument();
        plist.ReadFromFile(plistPath);
        var root = plist.root;
        root.SetString("CFBundleDisplayName", "Greymeat");
        // Application.version on iOS is CFBundleShortVersionString: the game builds its Photon game version from it
        root.SetString("CFBundleShortVersionString", "1.3.4");
        root.SetBoolean("UIFileSharingEnabled", true);           // Documents visible in Files / Filza
        root.SetBoolean("LSSupportsOpeningDocumentsInPlace", true);
        root.SetBoolean("UISupportsDocumentBrowser", true);
        root.SetBoolean("CADisableMinimumFrameDurationOnPhone", true);   // allow 120 Hz on ProMotion
        root.SetBoolean("UIRequiresFullScreen", true);
        root.SetBoolean("UIStatusBarHidden", true);
        root.SetBoolean("ITSAppUsesNonExemptEncryption", false);
        root.SetBoolean("GCSupportsControllerUserInteraction", true);
        root.SetBoolean("GCSupportsGameMode", true);
        
        // local Wi-Fi multiplayer (later): the host runs a game server, joining phones find it on the network
        root.SetString("NSLocalNetworkUsageDescription", "Greymeat uses your Wi-Fi for LAN games.");
        var gcProfiles = root.CreateArray("GCSupportedGameControllers");
        gcProfiles.AddDict().SetString("ProfileName", "ExtendedGamepad");
        var orientations = root.CreateArray("UISupportedInterfaceOrientations");
        orientations.AddString("UIInterfaceOrientationLandscapeRight");
        orientations.AddString("UIInterfaceOrientationLandscapeLeft");
        plist.WriteToFile(plistPath);

        // ---- Unity's native console output (and NSLog) -> Documents/Greymeat-native.log (previous run:
        // -prev), so a failure before any managed code runs (data loading, IL2CPP init) still leaves a log. ----
        string mainPath = Path.Combine(path, "MainApp", "main.mm");
        if (!File.Exists(mainPath)) mainPath = Path.Combine(path, "Classes", "main.mm");
        string main = File.ReadAllText(mainPath);
        const string hook = "static void GreyRedirectNativeLog()";
        if (!main.Contains(hook))
        {
            string fn = hook + @"
{
    NSString* docs = [NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES) firstObject];
    if (docs == nil) return;
    NSString* cur = [docs stringByAppendingPathComponent: @""Greymeat-native.log""];
    NSString* prev = [docs stringByAppendingPathComponent: @""Greymeat-native-prev.log""];
    NSFileManager* fm = [NSFileManager defaultManager];
    [fm removeItemAtPath: prev error: nil];
    [fm moveItemAtPath: cur toPath: prev error: nil];
    if (freopen([cur fileSystemRepresentation], ""w"", stderr) != NULL)
    {
        setvbuf(stderr, NULL, _IONBF, 0);
        dup2(fileno(stderr), fileno(stdout));
        setvbuf(stdout, NULL, _IOLBF, 0);
    }
}

";
            int at = main.IndexOf("int main(", StringComparison.Ordinal);
            if (at < 0) throw new BuildFailedException("main() not found in " + mainPath);
            main = main.Insert(at, fn);
            int brace = main.IndexOf('{', main.IndexOf("int main(", StringComparison.Ordinal));
            main = main.Insert(brace + 1, "\n    GreyRedirectNativeLog();");
            if (!main.Contains("#include <stdio.h>")) main = "#include <stdio.h>\n#include <unistd.h>\n" + main;
            File.WriteAllText(mainPath, main);
            Debug.Log("[IosShellBuild] native log redirect added to " + mainPath);
        }

        // ---- Xcode project: frameworks, unsigned (TrollStore install; CI fakesigns with ldid afterwards) ----
        string projPath = PBXProject.GetPBXProjectPath(path);
        var proj = new PBXProject();
        proj.ReadFromFile(projPath);
        string mainTarget = proj.GetUnityMainTargetGuid(), fw = proj.GetUnityFrameworkTargetGuid();
        foreach (var f in new[] { "GameController.framework", "AVFoundation.framework", "AudioToolbox.framework" })
            proj.AddFrameworkToProject(fw, f, false);
        proj.AddBuildProperty(fw, "OTHER_LDFLAGS", "-lc++");
        foreach (var guid in new[] { mainTarget, fw })
        {
            proj.SetBuildProperty(guid, "CODE_SIGNING_ALLOWED", "NO");
            proj.SetBuildProperty(guid, "CODE_SIGNING_REQUIRED", "NO");
            proj.SetBuildProperty(guid, "CODE_SIGN_IDENTITY", "");
            proj.SetBuildProperty(guid, "IPHONEOS_DEPLOYMENT_TARGET", "15.0");
            proj.AddBuildProperty(guid, "WARNING_CFLAGS", "-Wno-error");
            proj.SetBuildProperty(guid, "GCC_TREAT_WARNINGS_AS_ERRORS", "NO");
        }
        proj.WriteToFile(projPath);

        // report where the trampoline reads the thermal setting (in case the player setting is not enough)
        foreach (var f in Directory.GetFiles(path, "*.*", SearchOption.AllDirectories))
        {
            if (!(f.EndsWith(".mm") || f.EndsWith(".m") || f.EndsWith(".h") || f.EndsWith(".cpp"))) continue;
            if (f.Contains("Il2CppOutputProject")) continue;
            var lines = File.ReadAllLines(f);
            for (int i = 0; i < lines.Length; i++)
                if (lines[i].IndexOf("hermal", StringComparison.OrdinalIgnoreCase) >= 0)
                    Debug.Log($"[IosShellBuild] thermal ref {f.Substring(path.Length)}:{i + 1}: {lines[i].Trim()}");
        }
        Debug.Log("[IosShellBuild] Xcode project post-processed (plist, native log, frameworks, unsigned)");
    }
}
#endif

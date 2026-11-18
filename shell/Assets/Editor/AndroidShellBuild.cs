#if UNITY_ANDROID
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Android;
using UnityEditor.Build;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

// Android counterpart of IosShellBuild: an IL2CPP arm64 OpenGL ES 3 APK holding the patched game assemblies.
// CI swaps the converted game data into assets/bin/Data afterwards and re-signs the APK.
public static class AndroidShellBuild
{
    const string ScenePath = "Assets/Scenes/Boot.unity";
    static readonly NamedBuildTarget Target = NamedBuildTarget.Android;

    static string Env(string k) { var v = Environment.GetEnvironmentVariable(k); return string.IsNullOrEmpty(v) ? null : v; }

    public static void Build()
    {
        string outApk = Env("SHELL_OUT") ?? "Build/Greymeat.apk";
        if (!File.Exists(ScenePath))
        {
            Directory.CreateDirectory("Assets/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            EditorSceneManager.SaveScene(scene, ScenePath);
        }
        EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);

        if (Env("ANDROID_SDK_ROOT") is string sdk) AndroidExternalToolsSettings.sdkRootPath = sdk;
        if (Env("GREY_NDK") is string ndk) AndroidExternalToolsSettings.ndkRootPath = ndk;
        if (Env("GREY_JDK") is string jdk) AndroidExternalToolsSettings.jdkRootPath = jdk;
        Debug.Log($"[AndroidShellBuild] sdk={AndroidExternalToolsSettings.sdkRootPath} ndk={AndroidExternalToolsSettings.ndkRootPath} jdk={AndroidExternalToolsSettings.jdkRootPath} gradle={AndroidExternalToolsSettings.gradlePath}");

        PlayerSettings.companyName = "Greymeat";
        PlayerSettings.productName = "Greymeat";
        PlayerSettings.SetApplicationIdentifier(Target, "com.greymeat.androidport");
        PlayerSettings.bundleVersion = "1.3.5";
        int.TryParse(Env("GITHUB_RUN_NUMBER") ?? "1", out int code);
        PlayerSettings.Android.bundleVersionCode = Math.Max(1, code);
        PlayerSettings.SetScriptingBackend(Target, ScriptingImplementation.IL2CPP);
        PlayerSettings.SetManagedStrippingLevel(Target, ManagedStrippingLevel.Minimal);
        PlayerSettings.stripEngineCode = false;
        PlayerSettings.SetIl2CppCompilerConfiguration(Target, Il2CppCompilerConfiguration.Release);
        PlayerSettings.SetApiCompatibilityLevel(Target, ApiCompatibilityLevel.NET_Unity_4_8);
        PlayerSettings.allowUnsafeCode = true;
        PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64; // Unity 6000.6 dropped x86_64
        PlayerSettings.Android.minSdkVersion = (AndroidSdkVersions)26; // Unity 6000.6 minimum
        PlayerSettings.Android.useCustomKeystore = false;
        PlayerSettings.insecureHttpOption = InsecureHttpOption.AlwaysAllowed; // game update check uses http (PlayerSettings blob is copied into the game data)
        PlayerSettings.Android.renderOutsideSafeArea = true;
        PlayerSettings.Android.startInFullscreen = true;
        EditorUserBuildSettings.buildAppBundle = false;
        PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.Android, false);
        PlayerSettings.SetGraphicsAPIs(BuildTarget.Android, new[] { GraphicsDeviceType.OpenGLES3 });
        PlayerSettings.colorSpace = Env("COLOR_SPACE") == "Gamma" ? ColorSpace.Gamma : ColorSpace.Linear;
        PlayerSettings.gpuSkinning = true;
        PlayerSettings.MTRendering = true;
        PlayerSettings.statusBarHidden = true;
        PlayerSettings.defaultInterfaceOrientation = UIOrientation.AutoRotation;
        PlayerSettings.allowedAutorotateToLandscapeLeft = true;
        PlayerSettings.allowedAutorotateToLandscapeRight = true;
        PlayerSettings.allowedAutorotateToPortrait = false;
        PlayerSettings.allowedAutorotateToPortraitUpsideDown = false;
        PlayerSettings.SplashScreen.show = false;
        PlayerSettings.SplashScreen.showUnityLogo = false;
        PlayerSettings.SetStackTraceLogType(LogType.Log, StackTraceLogType.None);
        PlayerSettings.SetStackTraceLogType(LogType.Warning, StackTraceLogType.None);
        PlayerSettings.SetStackTraceLogType(LogType.Error, StackTraceLogType.ScriptOnly);
        PlayerSettings.SetStackTraceLogType(LogType.Exception, StackTraceLogType.ScriptOnly);
        PlayerSettings.usePlayerLog = true;
        SetProjectSetting("activeInputHandler", 0);

        var plugins = Directory.Exists("Assets/Plugins/Game") ? Directory.GetFiles("Assets/Plugins/Game", "*.dll").Length : 0;
        Debug.Log($"[AndroidShellBuild] {plugins} game assemblies");

        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outApk)));
        HybridSetup.Prebuild(ScenePath);   // mod DLL support (HybridCLR)

        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { ScenePath },
            locationPathName = outApk,
            target = BuildTarget.Android,
            targetGroup = BuildTargetGroup.Android,
            options = BuildOptions.None,
        });
        Debug.Log("[AndroidShellBuild] result: " + report.summary.result + " errors=" + report.summary.totalErrors);
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded) EditorApplication.Exit(1);
    }

    static void SetProjectSetting(string name, int value)
    {
        var asset = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset").FirstOrDefault();
        if (asset == null) return;
        var so = new SerializedObject(asset);
        var p = so.FindProperty(name);
        if (p == null) { Debug.LogWarning("[AndroidShellBuild] project setting missing: " + name); return; }
        p.intValue = value;
        so.ApplyModifiedPropertiesWithoutUndo();
    }
}
#endif

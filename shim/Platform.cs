using System;
using System.Diagnostics;
using UnityEngine;
using Debug = UnityEngine.Debug;

namespace GreyShim
{
    /// <summary>Stand-ins for desktop-only calls the patcher redirects here.</summary>
    public static class Platform
    {
        public static Process ProcessStart(ProcessStartInfo info)
        {
            string f = info?.FileName ?? "";
            if (f.StartsWith("http://") || f.StartsWith("https://")) { Application.OpenURL(f); return null; }
            Debug.LogWarning("[Greymeat-iOS] Process.Start blocked: " + f + " " + (info?.Arguments ?? ""));
            return null;
        }
        public static Process ProcessStart(string file)
        {
            if (file != null && (file.StartsWith("http://") || file.StartsWith("https://"))) { Application.OpenURL(file); return null; }
            Debug.LogWarning("[Greymeat-iOS] Process.Start blocked: " + file);
            return null;
        }
        public static Process ProcessStart(string file, string args)
        {
            Debug.LogWarning("[Greymeat-iOS] Process.Start blocked: " + file + " " + args);
            return null;
        }
        public static bool ProcessStartInstance(Process p) => false;

        /// NativeFilePicker.Pick (Windows open-file dialog): on iOS mods / maps are copied into Documents via Files.
        public static string PickFile(bool zip)
        {
            Debug.Log("[Greymeat-iOS] file picker not available; put files in Documents (Files app)");
            return "";
        }
        public static Process[] GetProcessesByName(string name) => new Process[0];

        /// Application.dataPath in game code: the game writes beside its Data folder (dataPath/../Maps, Replays,
        /// Screenshots, Data/Resources/runtime.cache). On a phone that is the read-only app package, so the game
        /// sees <persistentDataPath>/Data instead and those folders land in persistentDataPath (iOS: Documents).
        static string s_DataPath;
        public static string DataPath()
        {
            if (s_DataPath == null)
            {
                s_DataPath = System.IO.Path.Combine(Application.persistentDataPath, "Data");
                try { System.IO.Directory.CreateDirectory(s_DataPath); } catch { }
            }
            return s_DataPath;
        }

        /// The game's own StreamingAssets files (SurveillanceTakes.json.gz, ...) are shipped by CI in StreamingAssets/game.
        /// The game reads them with File.*, which can't see inside the APK, so on Android they are extracted first.
        static string s_Streaming;
        public static string StreamingAssetsPath()
        {
            if (s_Streaming != null) return s_Streaming;
            if (Application.platform != RuntimePlatform.Android)
                return s_Streaming = System.IO.Path.Combine(Application.streamingAssetsPath, "game");
            string dir = System.IO.Path.Combine(Application.persistentDataPath, "StreamingAssets");
            try
            {
                System.IO.Directory.CreateDirectory(dir);
                string stamp = System.IO.Path.Combine(dir, ".version"), ver = Application.version + "/" + Application.buildGUID;
                if (!System.IO.File.Exists(stamp) || System.IO.File.ReadAllText(stamp) != ver)
                {
                    int n = 0;
                    foreach (var kv in Streaming.Files("game", "")) { System.IO.File.WriteAllBytes(System.IO.Path.Combine(dir, kv.Key), kv.Value); n++; }
                    System.IO.File.WriteAllText(stamp, ver);
                    Debug.Log("[Greymeat] extracted " + n + " streaming asset(s)");
                }
            }
            catch (Exception e) { Debug.LogWarning("[Greymeat] streaming assets not extracted: " + e.Message); }
            return s_Streaming = dir;
        }

        /// Maps shipped with the game (Greymeat/Maps, copied to StreamingAssets/maps by CI) -> persistentDataPath/Maps, once.
        public static void InstallBundledMaps()
        {
            try
            {
                string dir = System.IO.Path.Combine(Application.persistentDataPath, "Maps");
                System.IO.Directory.CreateDirectory(dir);
                int n = 0;
                foreach (var kv in Streaming.Files("maps", ".gmmap"))
                {
                    string f = System.IO.Path.Combine(dir, kv.Key);
                    if (System.IO.File.Exists(f)) continue;
                    System.IO.File.WriteAllBytes(f, kv.Value);
                    n++;
                }
                if (n > 0) Debug.Log("[Greymeat] installed " + n + " bundled map(s)");
            }
            catch (Exception e) { Debug.LogWarning("[Greymeat] bundled maps not installed: " + e.Message); }
        }

        public static Action BeforeQuit;
        public static void Quit()
        {
            Debug.Log("[Greymeat-iOS] Application.Quit requested");
            try { BeforeQuit?.Invoke(); } catch { }
            Application.Quit();
        }
        public static void Quit(int code) => Quit();

        /// Main menu version line (Menu.MainCards: "alpha V… / LEV FILIPPOV" up to 1.3.5, "alpha 1.3.6" top-right since): draws it, then the port credit after it in smaller text.
        public const string PortCredit = "mobile port by piece89 (Discord)";
        static GUIStyle s_Credit;
        static GUIStyle s_CreditSrc;
        public static void CreatorLabel(Rect r, string text, GUIStyle style)
        {
            GUI.Label(r, text, style);
            if (style == null) return;
            if (s_Credit == null || s_CreditSrc != style)
            {
                s_CreditSrc = style;
                int fs = style.fontSize > 0 ? style.fontSize : 14;
                s_Credit = new GUIStyle(style) { fontSize = Mathf.Max(8, Mathf.RoundToInt(fs * 0.72f)), wordWrap = false, clipping = TextClipping.Overflow };
            }
            float w = style.CalcSize(new GUIContent(text)).x;
            float dy = style.lineHeight - s_Credit.lineHeight;
            switch (style.alignment)
            {
                case TextAnchor.UpperLeft: case TextAnchor.UpperCenter: case TextAnchor.UpperRight: break;
                case TextAnchor.MiddleLeft: case TextAnchor.MiddleCenter: case TextAnchor.MiddleRight: dy *= 0.5f; break;
                default: dy = 0; break;
            }
            var old = GUI.color;
            GUI.color = new Color(old.r, old.g, old.b, old.a * 0.7f);
            bool right = style.alignment == TextAnchor.UpperRight || style.alignment == TextAnchor.MiddleRight || style.alignment == TextAnchor.LowerRight;
            if (right)   // 1.3.6 home screen: version is right-aligned at the top-right, credit goes to its left
                GUI.Label(new Rect(r.xMax - w - 14f - 500f, r.y + dy * 0.85f, 500f, r.height), PortCredit + "   /", s_Credit);
            else
                GUI.Label(new Rect(r.x + w + 14f, r.y + dy * 0.85f, 400f, r.height), "/   " + PortCredit, s_Credit);
            GUI.color = old;
        }

        public static void SetResolution(int width, int height, bool fullscreen) { }
        public static void SetResolution(int width, int height, FullScreenMode mode) { }
        public static void SetResolution(int width, int height, FullScreenMode mode, RefreshRate rate) { }
        public static void SetResolution(int width, int height, FullScreenMode mode, int rate) { }
        public static void SetResolution(int width, int height, bool fullscreen, int rate) { }
        public static void SetFullScreen(bool v) { }
        public static bool GetFullScreen() => true;

        /// The game asks for vsync 1 / caps; on iOS we always run at the display rate (up to 120 Hz).
        public static int FrameRate = -1;
        public static void SetTargetFrameRate(int fps) { ApplyFrameRate(); }
        public static void SetVSyncCount(int n) { ApplyFrameRate(); }
        public static void ApplyFrameRate()
        {
            // mobile Unity defaults to 30 fps; ask for 120 (ProMotion / 120 Hz Android), the display caps it otherwise.
            // currentResolution often reports 60 on 120 Hz phones, so it only raises the target, never lowers it.
            int hz = FrameRate > 0 ? FrameRate : Mathf.Max(120, (int)Math.Round(Screen.currentResolution.refreshRateRatio.value));
            if (QualitySettings.vSyncCount != 0) QualitySettings.vSyncCount = 0;
            if (Application.targetFrameRate != hz) Application.targetFrameRate = hz;
        }
    }

    public static class VCursor
    {
        public static CursorLockMode LockState = CursorLockMode.None;
        public static bool Visible = true;
        public static int ChangedFrame;
        public static void set_lockState(CursorLockMode m) { if (m != LockState) ChangedFrame = Time.frameCount; LockState = m; }
        public static CursorLockMode get_lockState() => LockState;
        public static void set_visible(bool v) { if (v != Visible) ChangedFrame = Time.frameCount; Visible = v; }
        public static bool get_visible() => Visible;
    }
}

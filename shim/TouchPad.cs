using System;
using System.Collections.Generic;
using UnityEngine;

namespace GreyShim
{
    /// <summary>
    /// Greymeat touch controls. Fingers become the virtual keys / mouse the patched game reads through VInput.
    /// In menus (cursor free) a finger is the mouse (tap = left click, drag = drag, two fingers = scroll).
    /// In game (cursor locked): left half = floating move stick (WASD), right half = look (mouse delta),
    /// buttons press the game's default keys (Greymeat's GameInput resolves rebinds itself).
    /// </summary>
    /// Android: the player log only goes to logcat, so mirror it to
    /// Android/data/com.greymeat.androidport/files/Greymeat.log (previous run: Greymeat-prev.log).
    static class FileLog
    {
        static System.IO.StreamWriter s_W;
        static readonly object s_Lock = new object();
        public static void Start()
        {
            try
            {
                string dir = Application.persistentDataPath, cur = System.IO.Path.Combine(dir, "Greymeat.log"), prev = System.IO.Path.Combine(dir, "Greymeat-prev.log");
                if (System.IO.File.Exists(prev)) System.IO.File.Delete(prev);
                if (System.IO.File.Exists(cur)) System.IO.File.Move(cur, prev);
                s_W = new System.IO.StreamWriter(cur, false) { AutoFlush = true };
                Application.logMessageReceivedThreaded += (msg, st, type) =>
                {
                    lock (s_Lock)
                    {
                        s_W.WriteLine(type == LogType.Log ? msg : type + ": " + msg);
                        if (type == LogType.Exception || type == LogType.Error) s_W.WriteLine(st);
                    }
                };
            }
            catch (Exception e) { Debug.LogWarning("[Greymeat] file log: " + e.Message); }
        }
    }

    [DefaultExecutionOrder(-32000)]
    public sealed class TouchPad : MonoBehaviour
    {
        enum Kind { Hold, Toggle, Tap }

        sealed class Btn
        {
            public string Label;
            public KeyCode Key;
            public int Mouse = -1;
            public int Wheel;            // +1 up / -1 down
            public Kind Kind;
            public int Page;             // 0 main, 1 "more" page, -1 always, -2 menus only
            public Action OnTap;
            public float X, Y, R;        // GUI pixels (top-left origin) centre + radius as fraction of height
            public bool Held, On;
            public Rect Rect(float h) => new Rect(X - R * h, Y - R * h, 2 * R * h, 2 * R * h);
        }

        static TouchPad s_I;
        readonly List<Btn> m_Btns = new List<Btn>();
        readonly Dictionary<int, Btn> m_BtnFinger = new Dictionary<int, Btn>();
        int m_Stick = -1, m_Look = -1, m_Pointer = -1;
        Vector2 m_StickO, m_StickP, m_LastLook, m_LastScroll;
        bool m_Scroll;
        int m_Page;
        Btn m_MenuRmb;
        public static float LookSpeed = 1f, Alpha = 0.55f;
        Texture2D m_Circle;
        GUIStyle m_Style;
        int m_LastW, m_LastH;

        public static bool OnDevice => Application.platform == RuntimePlatform.IPhonePlayer || Application.platform == RuntimePlatform.Android;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        static void Boot()
        {
            if (s_I != null) return;
            if (Application.platform == RuntimePlatform.Android) FileLog.Start();
            Platform.ApplyFrameRate();
            Screen.sleepTimeout = SleepTimeout.NeverSleep;
            Input.multiTouchEnabled = true;
            LookSpeed = PlayerPrefs.GetFloat("ios_look", 1f);
            Alpha = PlayerPrefs.GetFloat("ios_alpha", 0.55f);
            var go = new GameObject("Greymeat-iOS touch");
            DontDestroyOnLoad(go);
            s_I = go.AddComponent<TouchPad>();
            VInput.Virtual = OnDevice;
            Debug.Log($"[Greymeat-iOS] touch={OnDevice} screen={Screen.width}x{Screen.height} gpu={SystemInfo.graphicsDeviceName} api={SystemInfo.graphicsDeviceType}");
        }

        void Add(string l, KeyCode k, int page, float x, float y, float r, Kind kind = Kind.Hold, int mouse = -1, int wheel = 0, Action tap = null)
            => m_Btns.Add(new Btn { Label = l, Key = k, Page = page, X = x, Y = y, R = r, Kind = kind, Mouse = mouse, Wheel = wheel, OnTap = tap });

        // positions: x as fraction of width from the right (negative) or left (positive), y fraction of height
        void Layout()
        {
            m_Btns.Clear();
            float w = Screen.width, h = Screen.height;
            Rect s = Screen.safeArea;
            float R(float f) => s.xMax - f * h;      // from right edge
            float L(float f) => s.xMin + f * h;      // from left edge
            float T(float f) => (h - s.yMax) + f * h;
            float B(float f) => (h - s.yMin) - f * h;
            float big = 0.085f, mid = 0.065f, sm = 0.05f;
            // right thumb cluster
            Add("FIRE", KeyCode.None, 0, R(0.13f), B(0.40f), big, mouse: 0);
            Add("RMB", KeyCode.None, 0, R(0.33f), B(0.47f), mid, mouse: 1);        // aim / right claw
            Add("FISTS", KeyCode.X, 0, R(0.50f), B(0.50f), sm, Kind.Tap);          // holster = fists
            Add("X-RAY", KeyCode.F3, 0, R(0.54f), T(0.10f), sm, Kind.Tap);
            Add("JUMP", KeyCode.Space, 0, R(0.13f), B(0.15f), big);
            Add("CROUCH", KeyCode.LeftControl, 0, R(0.33f), B(0.10f), mid, Kind.Toggle);
            Add("RUN", KeyCode.LeftShift, 0, L(0.40f), B(0.40f), mid, Kind.Toggle);
            Add("RELOAD", KeyCode.R, 0, R(0.33f), B(0.28f), sm);
            Add("KICK", KeyCode.F, 0, R(0.50f), B(0.10f), sm);
            Add("L.HAND", KeyCode.Q, 0, R(0.50f), B(0.30f), sm);
            Add("R.HAND", KeyCode.E, 0, R(0.13f), B(0.62f), sm);
            Add("WPN+", KeyCode.None, 0, R(0.30f), T(0.10f), sm, Kind.Tap, wheel: 1);
            Add("WPN-", KeyCode.None, 0, R(0.42f), T(0.10f), sm, Kind.Tap, wheel: -1);
            // always visible
            Add("MENU", KeyCode.Escape, -1, L(0.08f), T(0.08f), sm, Kind.Tap);
            Add("MORE", KeyCode.None, -1, R(0.08f), T(0.10f), sm, Kind.Tap, tap: () => m_Page = 1 - m_Page);
            // menus / editor: while on, the finger presses the right mouse button instead of the left
            m_MenuRmb = new Btn { Label = "RMB", Key = KeyCode.None, Page = -2, X = L(0.08f), Y = T(0.22f), R = sm, Kind = Kind.Toggle, On = m_MenuRmb != null && m_MenuRmb.On };
            m_Btns.Add(m_MenuRmb);
            // page 1: everything else (grid)
            string[] labels = { "LIE", "THROW", "BUTT", "ITEM", "PICK ITEM", "HOLSTER", "LIGHT", "ROPE", "SLOW", "VIEW", "SPAWN", "REGEN", "GOD", "FLY", "ALLY", "X-RAY", "SUPPR", "SELF AIM", "CHAT", "PHOTO",
                                "1", "2", "3", "4", "5", "6", "LOOK-", "LOOK+", "ALPHA" };
            KeyCode[] keys = { KeyCode.LeftAlt, KeyCode.Mouse2, KeyCode.L, KeyCode.B, KeyCode.V, KeyCode.X, KeyCode.G, KeyCode.H, KeyCode.CapsLock, KeyCode.F5, KeyCode.Tab, KeyCode.Y, KeyCode.F2, KeyCode.BackQuote, KeyCode.I, KeyCode.F3, KeyCode.Z, KeyCode.J, KeyCode.T, KeyCode.F4,
                               KeyCode.Alpha1, KeyCode.Alpha2, KeyCode.Alpha3, KeyCode.Alpha4, KeyCode.Alpha5, KeyCode.Alpha6, KeyCode.None, KeyCode.None, KeyCode.None };
            int cols = 6;
            float gx = s.xMin + 0.30f * h, gy = T(0.24f), step = 0.13f * h;
            for (int i = 0; i < labels.Length; i++)
            {
                float x = gx + (i % cols) * step, y = gy + (i / cols) * step;
                Action tap = null;
                Kind kind = labels[i] == "SELF AIM" ? Kind.Hold : Kind.Tap;
                if (labels[i] == "LOOK-") tap = () => SetLook(LookSpeed / 1.2f);
                if (labels[i] == "LOOK+") tap = () => SetLook(LookSpeed * 1.2f);
                if (labels[i] == "ALPHA") tap = () => { Alpha = Alpha > 0.8f ? 0.2f : Alpha + 0.2f; PlayerPrefs.SetFloat("ios_alpha", Alpha); };
                Add(labels[i], keys[i], 1, x, y, 0.055f, kind, tap: tap);
            }
        }

        static void SetLook(float v) { LookSpeed = Mathf.Clamp(v, 0.2f, 5f); PlayerPrefs.SetFloat("ios_look", LookSpeed); }

        static bool InGame => VCursor.LockState == CursorLockMode.Locked;

        void Update()
        {
            if (!OnDevice) return;
            if (Time.frameCount % 60 == 0) Platform.ApplyFrameRate();   // keep the frame cap off whatever reset it
            if (Screen.width != m_LastW || Screen.height != m_LastH) { m_LastW = Screen.width; m_LastH = Screen.height; Layout(); }
            float h = Screen.height;
            bool game = InGame;
            if (!game) m_Page = 0;

            // release last frame's held keys (hold buttons and toggles); they are set again below while held / on
            foreach (var b in m_Btns)
                if (b.Kind != Kind.Tap && b.Key != KeyCode.None) VInput.SetKey(b.Key, false);
            VInput.SetKey(KeyCode.W, false); VInput.SetKey(KeyCode.A, false); VInput.SetKey(KeyCode.S, false); VInput.SetKey(KeyCode.D, false);
            for (int i = 0; i < 3; i++) VInput.SetMouseButton(i, false);

            int free = 0; Vector2 sum = Vector2.zero; bool havePrim = false; Touch prim = default;
            for (int i = 0; i < Input.touchCount; i++)
            {
                var t = Input.GetTouch(i);
                var gp = new Vector2(t.position.x, h - t.position.y);   // GUI coords
                bool ended = t.phase == TouchPhase.Ended || t.phase == TouchPhase.Canceled;
                if (t.phase == TouchPhase.Began)
                {
                    Btn hit = null;
                    foreach (var b in m_Btns)
                        if (Visible(b, game) && b.Rect(h).Contains(gp)) { hit = b; break; }
                    if (hit != null)
                    {
                        m_BtnFinger[t.fingerId] = hit;
                        if (hit.Kind == Kind.Toggle) { hit.On = !hit.On; }
                        else if (hit.Kind == Kind.Tap) Fire(hit);
                    }
                    else if (game && m_Page == 0 && gp.x < Screen.width * 0.42f && m_Stick < 0) { m_Stick = t.fingerId; m_StickO = m_StickP = gp; }
                    else if (game && m_Look < 0) { m_Look = t.fingerId; m_LastLook = t.position; }
                }
                if (m_BtnFinger.TryGetValue(t.fingerId, out var bb))
                {
                    if (ended) m_BtnFinger.Remove(t.fingerId);
                    else if (bb.Kind == Kind.Hold) Hold(bb);
                    continue;
                }
                if (t.fingerId == m_Stick)
                {
                    if (ended || !game) { m_Stick = -1; continue; }
                    m_StickP = gp;
                    Vector2 d = (m_StickP - m_StickO) / (0.09f * h);
                    if (d.magnitude > 1.6f) m_StickO = m_StickP - d.normalized * 1.6f * 0.09f * h;   // floating stick follows
                    if (d.y < -0.35f) VInput.SetKey(KeyCode.W, true);
                    if (d.y > 0.35f) VInput.SetKey(KeyCode.S, true);
                    if (d.x < -0.35f) VInput.SetKey(KeyCode.A, true);
                    if (d.x > 0.35f) VInput.SetKey(KeyCode.D, true);
                    continue;
                }
                if (t.fingerId == m_Look)
                {
                    if (ended || !game) { m_Look = -1; continue; }
                    VInput.VMouseDelta += (t.position - m_LastLook) * (LookSpeed * 1600f / Mathf.Max(600f, h));
                    m_LastLook = t.position;
                    continue;
                }
                if (game || ended) { if (t.fingerId == m_Pointer) m_Pointer = -1; continue; }
                // menu pointer
                free++; sum += t.position;
                if (m_Pointer < 0 && !m_Scroll) m_Pointer = t.fingerId;
                if (t.fingerId == m_Pointer) { prim = t; havePrim = true; }
            }
            foreach (var b in m_Btns)
                if (b.Kind == Kind.Toggle && b.On && b.Key != KeyCode.None)
                {
                    if (!game) b.On = false; else VInput.SetKey(b.Key, true);
                }

            if (!game)
            {
                if (free >= 2)
                {
                    Vector2 avg = sum / free;
                    if (m_Scroll) VInput.VScroll += new Vector2(0, (avg.y - m_LastScroll.y) / (h * 0.03f));
                    m_LastScroll = avg; m_Scroll = true; m_Pointer = -1;
                }
                else
                {
                    if (free == 0) m_Scroll = false;
                    if (!m_Scroll && havePrim)
                    {
                        VInput.VMousePosition = prim.position;
                        VInput.SetMouseButton(m_MenuRmb != null && m_MenuRmb.On ? 1 : 0, true);
                    }
                }
            }
            VInput.BeginFrame();
        }

        bool Visible(Btn b, bool game)
        {
            if (b.Page == -2) return !game;
            if (b.Page == -1) return b.Label != "MORE" || game;
            return game && b.Page == m_Page;
        }

        void Hold(Btn b)
        {
            if (b.Mouse >= 0) VInput.SetMouseButton(b.Mouse, true);
            else if (b.Key != KeyCode.None) VInput.SetKey(b.Key, true);
        }

        void Fire(Btn b)
        {
            if (b.OnTap != null) { b.OnTap(); return; }
            if (b.Wheel != 0) { VInput.VScroll += new Vector2(0, b.Wheel); return; }
            if (b.Mouse >= 0) { VInput.TapMouseButton(b.Mouse); return; }
            if (b.Key == KeyCode.Mouse2) { VInput.TapMouseButton(2); return; }
            if (b.Key != KeyCode.None) VInput.TapKey(b.Key, 2);
        }

        void OnApplicationPause(bool p) { if (p) { VInput.ReleaseAll(); PlayerPrefs.Save(); } }

        void OnGUI()
        {
            if (!OnDevice || Event.current.type != EventType.Repaint) return;
            GUI.depth = -1000;
            float h = Screen.height;
            bool game = InGame;
            if (m_Circle == null) m_Circle = MakeCircle(64);
            if (m_Style == null) m_Style = new GUIStyle(GUI.skin.label) { alignment = TextAnchor.MiddleCenter, fontStyle = FontStyle.Bold, wordWrap = false };
            foreach (var b in m_Btns)
            {
                if (!Visible(b, game)) continue;
                var r = b.Rect(h);
                bool lit = b.On || m_BtnFinger.ContainsValue(b);
                GUI.color = lit ? new Color(1f, 0.25f, 0.2f, Alpha + 0.2f) : new Color(0.08f, 0.08f, 0.08f, Alpha);
                GUI.DrawTexture(r, m_Circle);
                m_Style.fontSize = Mathf.RoundToInt(r.height * (b.Label.Length > 5 ? 0.17f : 0.24f));
                GUI.color = new Color(1, 1, 1, Mathf.Min(1f, Alpha + 0.35f));
                GUI.Label(r, b.Label, m_Style);
            }
            if (game && m_Stick >= 0)
            {
                float rr = 0.09f * h;
                GUI.color = new Color(1, 1, 1, Alpha * 0.5f);
                GUI.DrawTexture(new Rect(m_StickO.x - rr, m_StickO.y - rr, 2 * rr, 2 * rr), m_Circle);
                Vector2 k = m_StickO + Vector2.ClampMagnitude(m_StickP - m_StickO, rr);
                GUI.color = new Color(1, 1, 1, Alpha);
                GUI.DrawTexture(new Rect(k.x - rr * 0.45f, k.y - rr * 0.45f, rr * 0.9f, rr * 0.9f), m_Circle);
            }
            GUI.color = Color.white;
        }

        static Texture2D MakeCircle(int n)
        {
            var t = new Texture2D(n, n, TextureFormat.RGBA32, false);
            float c = (n - 1) / 2f;
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                {
                    float d = Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c;
                    t.SetPixel(x, y, new Color(1, 1, 1, Mathf.Clamp01((1f - d) * c)));
                }
            t.Apply();
            return t;
        }
    }
}

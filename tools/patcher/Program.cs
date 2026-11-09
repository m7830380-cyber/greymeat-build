// Patches the Greymeat (Windows, Mono) managed assemblies so IL2CPP can AOT-compile them for iOS.
//   GreyPatcher patch <managedDir> <GreyShim.dll> <outDir>
// * every UnityEngine.Input call in Assembly-CSharp -> GreyShim.VInput (touch layer drives it)
// * Cursor.lockState/visible -> GreyShim.VCursor; Process.Start / Screen / Quit / frame rate -> GreyShim.Platform
// * Win32 + Steam P/Invokes -> stubs (Steam returns 0/false, so SteamRooms.Init fails cleanly: no multiplayer via Steam)
// * NativeFilePicker.Pick -> GreyShim.Platform.PickFile (iOS: returns "" for now)
using Mono.Cecil;
using Mono.Cecil.Cil;
using Mono.Cecil.Rocks;

static partial class Program
{
    const string ShimName = "GreyShim";
    static readonly HashSet<string> InternalLibs = new HashSet<string>();
    static readonly HashSet<string> DefaultStubLibs = new HashSet<string> { "steam_api64", "steam_api", "crypt32", "kernel32", "comdlg32", "user32" };
    static readonly HashSet<string> InputRedirect = new HashSet<string> { "Assembly-CSharp", "Greymeat.ModApi" };

    static int Main(string[] args)
    {
        try
        {
            if (args.Length >= 4 && args[0] == "patch") return Patch(args[1], args[2], args[3]);
            if (args.Length >= 2 && args[0] == "verify") return Verify(args[1]);
        }
        catch (Exception e) { Console.Error.WriteLine("FATAL: " + e); return 3; }
        Console.Error.WriteLine("usage: GreyPatcher patch <managedDir> <GreyShim.dll> <outDir>");
        return 1;
    }

    static readonly string[] Shipped = { "Assembly-CSharp.dll", "Greymeat.ModApi.dll", "com.rlabrecque.steamworks.net.dll" };

    static int Patch(string inDir, string shimPath, string outDir)
    {
        Directory.CreateDirectory(outDir);
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(inDir);
        resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(shimPath)));
        var rp = new ReaderParameters { AssemblyResolver = resolver, ReadingMode = ReadingMode.Immediate, ReadSymbols = false };
        var shim = AssemblyDefinition.ReadAssembly(shimPath, rp);
        int total = 0, failures = 0;
        foreach (var name in Shipped)
        {
            var asm = AssemblyDefinition.ReadAssembly(Path.Combine(inDir, name), rp);
            var ctx = new Ctx(asm.MainModule, shim.MainModule);
            var counts = new List<string>();
            int Step(string what, int k) { if (k > 0) counts.Add($"{what} {k}"); return k; }
            int n = 0;
            string an = asm.Name.Name;
            if (an == "Assembly-CSharp") n += Step("game", PatchGame(ctx));
            n += Step("pinvoke", RetargetPInvokes(ctx));
            n += Step("process", RedirectProcess(ctx));
            n += Step("cursor", RedirectCursor(ctx));
            n += Step("screen", RedirectScreen(ctx));
            n += Step("typedref", RemoveTypedReferences(ctx));
            if (InputRedirect.Contains(an)) n += Step("input", RedirectInput(ctx));
            total += n; failures += ctx.Failures;
            asm.Write(Path.Combine(outDir, name));
            Console.WriteLine($"{name}: {n} patches ({string.Join(", ", counts)}){(ctx.Failures > 0 ? $", {ctx.Failures} FAILED" : "")}");
        }
        File.Copy(shimPath, Path.Combine(outDir, ShimName + ".dll"), true);
        Console.WriteLine($"{total} patches, {failures} failures");
        if (failures > 0) return 2;
        return Verify(outDir);
    }

    static int PatchGame(Ctx c)
    {
        int n = 0;
        var picker = c.Mod.GetType("NativeFilePicker");
        var pick = Method(c, picker, "Pick", 1);
        if (pick != null)
        {
            var il = NewBody(pick);
            il.Emit(OpCodes.Ldarg_0);
            il.Emit(OpCodes.Call, c.ShimMethod("Platform", "PickFile", "System.Boolean"));
            il.Emit(OpCodes.Ret);
            n++;
        }
        // Steam: Init() always reports "not available" (no Steam client on iOS); everything else is guarded by Initialized.
        var rooms = c.Mod.GetType("SteamRooms");
        var init = Method(c, rooms, "Init", 0);
        if (init != null)
        {
            var il = NewBody(init);
            il.Emit(OpCodes.Ldc_I4_0);
            il.Emit(OpCodes.Ret);
            n++;
        }
        n += MakeNoOp(c, rooms, "StartupInvite", 0);
        n += PortCredit(c);
        return n;
    }

    /// Main menu: the GUI.Label drawing "alpha V… / LEV FILIPPOV" -> Platform.CreatorLabel (adds the port credit beside it).
    static int PortCredit(Ctx c)
    {
        var main = Method(c, c.Mod.GetType("Menu"), "MainCards");
        if (main == null) return 0;
        var ins = main.Body.Instructions;
        for (int i = 0; i < ins.Count; i++)
        {
            if (ins[i].OpCode != OpCodes.Ldstr || !(ins[i].Operand is string s) || !s.Contains("FILIPPOV")) continue;
            for (int j = i + 1; j < ins.Count && j < i + 8; j++)
                if (ins[j].OpCode == OpCodes.Call && ins[j].Operand is MethodReference mr && mr.DeclaringType.FullName == "UnityEngine.GUI" &&
                    mr.Name == "Label" && Sig(mr) == "UnityEngine.Rect,System.String,UnityEngine.GUIStyle")
                {
                    ins[j].Operand = c.ShimMethod("Platform", "CreatorLabel", "UnityEngine.Rect", "System.String", "UnityEngine.GUIStyle");
                    return 1;
                }
        }
        c.Fail("Menu.MainCards: creator label not found");
        return 0;
    }

    static int Verify(string dir)
    {
        int problems = 0;
        foreach (var path in Directory.GetFiles(dir, "*.dll"))
        {
            string name = Path.GetFileNameWithoutExtension(path);
            if (name == ShimName) continue;
            var mod = ModuleDefinition.ReadModule(path);
            foreach (var t in mod.GetTypes())
                foreach (var m in t.Methods)
                {
                    if (m.IsPInvokeImpl && m.PInvokeInfo != null && m.PInvokeInfo.Module.Name != "__Internal")
                    { Console.WriteLine($"  VERIFY: {name}: P/Invoke {m.PInvokeInfo.Module.Name} in {m.FullName}"); problems++; }
                    if (!m.HasBody) continue;
                    foreach (var ins in m.Body.Instructions)
                        if (ins.Operand is MethodReference mr && mr.DeclaringType.FullName == "UnityEngine.Input" && mr.Name != ".ctor" && InputRedirect.Contains(name))
                        { Console.WriteLine($"  VERIFY: {name}: Input.{mr.Name} left in {m.FullName}"); problems++; }
                }
        }
        Console.WriteLine(problems == 0 ? "verify: OK" : $"verify: {problems} problem(s)");
        return problems == 0 ? 0 : 4;
    }

    // ------------------------------------------------------------------------------------------
    sealed class Ctx
    {
        public readonly ModuleDefinition Mod;
        public readonly ModuleDefinition Shim;
        public int Failures;
        public Ctx(ModuleDefinition mod, ModuleDefinition shim) { Mod = mod; Shim = shim; }

        public TypeDefinition ShimType(string name) => Shim.GetType(ShimName + "." + name) ?? throw new Exception("shim type missing: " + name);

        public MethodReference ShimMethod(string type, string name, params string[] paramTypes)
        {
            var m = ShimType(type).Methods.FirstOrDefault(x => x.Name == name && x.Parameters.Count == paramTypes.Length &&
                x.Parameters.Select(p => p.ParameterType.FullName).SequenceEqual(paramTypes));
            if (m == null) throw new Exception($"shim method missing: {type}.{name}({string.Join(",", paramTypes)})");
            return Mod.ImportReference(m);
        }

        public void Fail(string what)
        {
            Console.WriteLine("  !! " + what);
            Failures++;
        }
    }

    static IEnumerable<(MethodDefinition m, Instruction ins)> Calls(ModuleDefinition mod, Func<MethodDefinition, bool> where = null)
    {
        foreach (var t in mod.GetTypes())
            foreach (var m in t.Methods)
            {
                if (!m.HasBody || (where != null && !where(m))) continue;
                foreach (var ins in m.Body.Instructions.ToList())
                    if ((ins.OpCode == OpCodes.Call || ins.OpCode == OpCodes.Callvirt || ins.OpCode == OpCodes.Newobj) && ins.Operand is MethodReference)
                        yield return (m, ins);
            }
    }

    static string Sig(MethodReference mr) => string.Join(",", mr.Parameters.Select(p => p.ParameterType.FullName));

    static MethodReference DnfCtor(ModuleDefinition mod)
    {
        var dnfType = new TypeReference("System", "DllNotFoundException", mod, mod.TypeSystem.CoreLibrary);
        var ctor = new MethodReference(".ctor", mod.TypeSystem.Void, dnfType) { HasThis = true };
        ctor.Parameters.Add(new ParameterDefinition(mod.TypeSystem.String));
        return ctor;
    }

    // ------------------------------------------------------------------------------------------

    static int RetargetPInvokes(Ctx c)
    {
        int n = 0;
        var mod = c.Mod;
        ModuleReference internalRef = null;
        foreach (var t in mod.GetTypes())
            foreach (var m in t.Methods)
            {
                if (!m.IsPInvokeImpl || m.PInvokeInfo == null) continue;
                string lib = m.PInvokeInfo.Module.Name;
                if (lib == "__Internal") continue;
                string libKey = Path.GetFileNameWithoutExtension(lib).ToLowerInvariant();
                if (lib.EndsWith(".dll", StringComparison.OrdinalIgnoreCase) || lib.EndsWith(".so") || lib.EndsWith(".dylib")) { }
                else libKey = lib.ToLowerInvariant();
                if (libKey.StartsWith("lib")) libKey = libKey.Substring(3);
                string entry = m.PInvokeInfo.EntryPoint ?? m.Name;
                if (InternalLibs.Contains(libKey))
                {
                    if (internalRef == null)
                    {
                        internalRef = mod.ModuleReferences.FirstOrDefault(r => r.Name == "__Internal");
                        if (internalRef == null) { internalRef = new ModuleReference("__Internal"); mod.ModuleReferences.Add(internalRef); }
                    }
                    m.PInvokeInfo.Module = internalRef;
                    n++;
                    continue;
                }
                if (DefaultStubLibs.Contains(libKey)) StubPInvokeDefault(m);
                else StubPInvoke(m, $"{lib} ({entry}) is not available on iOS");
                n++;
            }
        // drop now-unused module references (steam_api64, ...)
        var used = new HashSet<ModuleReference>(mod.GetTypes().SelectMany(t => t.Methods).Where(m => m.IsPInvokeImpl && m.PInvokeInfo != null).Select(m => m.PInvokeInfo.Module));
        foreach (var r in mod.ModuleReferences.ToList())
            if (!used.Contains(r)) mod.ModuleReferences.Remove(r);
        return n;
    }

    static void ClearPInvoke(MethodDefinition m)
    {
        m.PInvokeInfo = null;     // Cecil's setter turns IsPInvokeImpl back on,
        m.IsPInvokeImpl = false;  // so clear the flag afterwards
        m.IsPreserveSig = false;
        m.ImplAttributes &= ~MethodImplAttributes.PreserveSig;
        m.ImplAttributes |= MethodImplAttributes.IL | MethodImplAttributes.Managed;
        // marshalling attributes mean nothing on a managed method
        m.MethodReturnType.MarshalInfo = null;
        foreach (var p in m.Parameters) p.MarshalInfo = null;
    }

    static void StubPInvoke(MethodDefinition m, string message)
    {
        ClearPInvoke(m);
        m.Body = new MethodBody(m);
        var il = m.Body.GetILProcessor();
        il.Emit(OpCodes.Ldstr, message);
        il.Emit(OpCodes.Newobj, DnfCtor(m.Module));
        il.Emit(OpCodes.Throw);
    }

    /// Stub that clears every out/ref argument and returns default(T).
    static void StubPInvokeDefault(MethodDefinition m)
    {
        ClearPInvoke(m);
        var il = NewBody(m);
        foreach (var p in m.Parameters)
            if (p.ParameterType is ByReferenceType br && p.IsOut)
            {
                il.Emit(OpCodes.Ldarg, p);
                il.Emit(OpCodes.Initobj, br.ElementType);
            }
        EmitDefault(m, il, m.ReturnType);
        il.Emit(OpCodes.Ret);
    }

    static void EmitDefault(MethodDefinition m, ILProcessor il, TypeReference t)
    {
        if (t.FullName == "System.Void") return;
        if (!t.IsValueType && !t.IsGenericParameter) { il.Emit(OpCodes.Ldnull); return; }
        var v = new VariableDefinition(t);
        m.Body.Variables.Add(v);
        m.Body.InitLocals = true;
        il.Emit(OpCodes.Ldloca, v);
        il.Emit(OpCodes.Initobj, t);
        il.Emit(OpCodes.Ldloc, v);
    }

    // ------------------------------------------------------------------------------------------
    static int RedirectProcess(Ctx c)
    {
        int n = 0;
        foreach (var (m, ins) in Calls(c.Mod))
        {
            var mr = (MethodReference)ins.Operand;
            if (mr.DeclaringType.FullName != "System.Diagnostics.Process" || ins.OpCode == OpCodes.Newobj) continue;
            MethodReference to = null;
            if (mr.Name == "Start" && !mr.HasThis)
            {
                string sig = Sig(mr);
                if (sig == "System.Diagnostics.ProcessStartInfo") to = c.ShimMethod("Platform", "ProcessStart", "System.Diagnostics.ProcessStartInfo");
                else if (sig == "System.String") to = c.ShimMethod("Platform", "ProcessStart", "System.String");
                else if (sig == "System.String,System.String") to = c.ShimMethod("Platform", "ProcessStart", "System.String", "System.String");
                else { c.Fail($"{m.FullName}: unhandled Process.Start({sig})"); continue; }
            }
            else if (mr.Name == "Start" && mr.HasThis && mr.Parameters.Count == 0)
                to = c.ShimMethod("Platform", "ProcessStartInstance", "System.Diagnostics.Process");
            else if (mr.Name == "GetProcessesByName" && Sig(mr) == "System.String")
                to = c.ShimMethod("Platform", "GetProcessesByName", "System.String");
            if (to == null) continue;
            ins.OpCode = OpCodes.Call;
            ins.Operand = to;
            n++;
        }
        return n;
    }

    // ------------------------------------------------------------------------------------------
    static int RedirectCursor(Ctx c)
    {
        int n = 0;
        var vc = c.ShimType("VCursor");
        foreach (var (m, ins) in Calls(c.Mod))
        {
            var mr = (MethodReference)ins.Operand;
            if (mr.DeclaringType.FullName != "UnityEngine.Cursor") continue;
            if (mr.Name != "get_lockState" && mr.Name != "set_lockState" && mr.Name != "get_visible" && mr.Name != "set_visible") continue;
            var target = vc.Methods.First(x => x.Name == mr.Name);
            ins.OpCode = OpCodes.Call;
            ins.Operand = c.Mod.ImportReference(target);
            n++;
        }
        return n;
    }

    /// Redirects every call to a static engine member to a shim method with the same signature.
    static int RedirectStatic(Ctx c, string type, string name, string shimType, string shimName, Func<MethodDefinition, bool> where = null)
    {
        int n = 0;
        var defs = c.ShimType(shimType).Methods.Where(x => x.Name == shimName).ToList();
        foreach (var (m, ins) in Calls(c.Mod, where))
        {
            var mr = (MethodReference)ins.Operand;
            if (mr.DeclaringType.FullName != type || mr.Name != name || mr.HasThis) continue;
            var def = defs.FirstOrDefault(x => Sig(x) == Sig(mr) && x.ReturnType.FullName == mr.ReturnType.FullName);
            if (def == null) { c.Fail($"{m.FullName}: no shim {shimType}.{shimName} for {type}.{name}({Sig(mr)})"); continue; }
            ins.OpCode = OpCodes.Call;
            ins.Operand = c.Mod.ImportReference(def);
            n++;
        }
        return n;
    }

    /// Redirects every static member of an engine class to the shim class member with the same name and signature.
    static int RedirectAll(Ctx c, string type, string shimType)
    {
        int n = 0;
        var st = c.ShimType(shimType);
        foreach (var (m, ins) in Calls(c.Mod))
        {
            var mr = (MethodReference)ins.Operand;
            if (mr.DeclaringType.FullName != type) continue;
            var t = st.Methods.FirstOrDefault(x => x.Name == mr.Name && x.IsStatic && Sig(x) == Sig(mr) && x.ReturnType.FullName == mr.ReturnType.FullName);
            if (t == null) { c.Fail($"{m.FullName}: no {shimType}.{mr.Name}({Sig(mr)})"); continue; }
            ins.OpCode = OpCodes.Call;
            ins.Operand = c.Mod.ImportReference(t);
            n++;
        }
        return n;
    }

    /// Desktop window / frame-rate calls: the iOS player is always fullscreen; frame pacing is the shim's.
    static int RedirectScreen(Ctx c)
    {
        int n = 0;
        foreach (var screen in new[] { "UnityEngine.Screen", "UnityEngine.Device.Screen" })
        {
            n += RedirectStatic(c, screen, "SetResolution", "Platform", "SetResolution");
            n += RedirectStatic(c, screen, "set_fullScreen", "Platform", "SetFullScreen");
            n += RedirectStatic(c, screen, "get_fullScreen", "Platform", "GetFullScreen");
        }
        n += RedirectStatic(c, "UnityEngine.Application", "Quit", "Platform", "Quit");
        n += RedirectStatic(c, "UnityEngine.Application", "set_targetFrameRate", "Platform", "SetTargetFrameRate");
        n += RedirectStatic(c, "UnityEngine.QualitySettings", "set_vSyncCount", "Platform", "SetVSyncCount");
        return n;
    }

    // ------------------------------------------------------------------------------------------
    static int RedirectInput(Ctx c)
    {
        int n = 0;
        var vinput = c.ShimType("VInput");
        foreach (var (m, ins) in Calls(c.Mod))
        {
            var mr = (MethodReference)ins.Operand;
            if (mr.DeclaringType.FullName != "UnityEngine.Input" || mr.Name == ".ctor") continue;   // "new Input()" (pworld) is harmless
            var target = vinput.Methods.FirstOrDefault(x => x.Name == mr.Name && x.IsStatic &&
                x.Parameters.Count == mr.Parameters.Count && Sig(x) == Sig(mr) && x.ReturnType.FullName == mr.ReturnType.FullName);
            if (target == null)
            {
                c.Fail($"{m.FullName}: no VInput equivalent for Input.{mr.Name}({Sig(mr)})");
                continue;
            }
            ins.OpCode = OpCodes.Call;
            ins.Operand = c.Mod.ImportReference(target);
            n++;
        }
        return n;
    }

    // ------------------------------------------------------------------------------------------
    // IL2CPP does not model mkrefany (C# __makeref) well - report any.
    static int RemoveTypedReferences(Ctx c)
    {
        foreach (var t in c.Mod.GetTypes())
            foreach (var m in t.Methods)
                if (m.HasBody && m.Body.Instructions.Any(i => i.OpCode.Code == Code.Mkrefany || i.OpCode.Code == Code.Refanyval ||
                                                              i.OpCode.Code == Code.Refanytype || i.OpCode.Code == Code.Arglist))
                    Console.WriteLine("  note: typed reference / arglist in " + m.FullName + " (IL2CPP may reject it)");
        return 0;
    }

    // ------------------------------------------------------------------------------------------
    static MethodDefinition Method(Ctx c, TypeDefinition t, string name, int paramCount = -1)
    {
        var m = t?.Methods.FirstOrDefault(x => x.Name == name && (paramCount < 0 || x.Parameters.Count == paramCount));
        if (m == null) c.Fail($"{t?.FullName ?? "<missing type>"}.{name} missing");
        return m;
    }

    static ILProcessor NewBody(MethodDefinition m)
    {
        m.Body = new MethodBody(m);
        return m.Body.GetILProcessor();
    }

    static int MakeNoOp(Ctx c, TypeDefinition t, string name, int paramCount = -1)
    {
        var m = Method(c, t, name, paramCount);
        if (m == null) return 0;
        if (m.ReturnType.FullName != "System.Void") { c.Fail($"{t.Name}.{name} is not void"); return 0; }
        NewBody(m).Emit(OpCodes.Ret);
        return 1;
    }

    static FieldDefinition Field(Ctx c, TypeDefinition t, string name)
    {
        var f = t?.Fields.FirstOrDefault(x => x.Name == name);
        if (f == null) c.Fail($"{t?.FullName}.{name} field missing");
        return f;
    }


}

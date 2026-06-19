using System.Diagnostics;
using System.Runtime.InteropServices;

internal static class Program
{
    private const string DefaultServiceName = "Yarbis";
    private const string DefaultInstanceId = "default";
    private const int ServiceWin32OwnProcess = 0x00000010;
    private const int ServiceStopped = 0x00000001;
    private const int ServiceStartPending = 0x00000002;
    private const int ServiceStopPending = 0x00000003;
    private const int ServiceRunning = 0x00000004;
    private const int ServiceAcceptStop = 0x00000001;
    private const int ServiceAcceptShutdown = 0x00000004;
    private const int ServiceControlStop = 0x00000001;
    private const int ServiceControlShutdown = 0x00000005;
    private const int NoError = 0;

    private static readonly ServiceMainDelegate ServiceMainCallback = ServiceMain;
    private static readonly HandlerExDelegate HandlerCallback = HandlerEx;
    private static readonly ManualResetEventSlim StopRequested = new(false);

    private static IntPtr _serviceStatusHandle = IntPtr.Zero;
    private static string _workspaceRoot = "";
    private static string _serviceName = DefaultServiceName;
    private static string _instanceId = DefaultInstanceId;
    private static Process? _childProcess;
    private static int _checkpoint = 1;

    public static int Main(string[] args)
    {
        _workspaceRoot = ResolveWorkspaceRoot(args);
        _instanceId = ResolveOption(args, "--instance") ?? Environment.GetEnvironmentVariable("YARBIS_INSTANCE") ?? DefaultInstanceId;
        _serviceName = ResolveOption(args, "--service-name") ?? Environment.GetEnvironmentVariable("YARBIS_SERVICE_NAME") ?? DefaultServiceName;

        if (args.Any(item => string.Equals(item, "--console", StringComparison.OrdinalIgnoreCase)))
        {
            return RunConsole();
        }

        var serviceTable = new[]
        {
            new ServiceTableEntry
            {
                ServiceName = _serviceName,
                ServiceMain = ServiceMainCallback,
            },
            new ServiceTableEntry
            {
                ServiceName = null,
                ServiceMain = null,
            },
        };

        if (!StartServiceCtrlDispatcher(serviceTable))
        {
            Log("StartServiceCtrlDispatcher fallo. Ejecutando en modo consola para diagnostico.");
            return RunConsole();
        }

        return 0;
    }

    private static string ResolveWorkspaceRoot(string[] args)
    {
        foreach (var arg in args)
        {
            if (string.IsNullOrWhiteSpace(arg) || arg.StartsWith("--", StringComparison.Ordinal))
            {
                continue;
            }

            var candidate = Path.GetFullPath(arg);
            if (File.Exists(Path.Combine(candidate, "yarbis_service.py")))
            {
                return candidate;
            }
        }

        var current = AppContext.BaseDirectory;
        var directory = new DirectoryInfo(current);
        while (directory is not null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "yarbis_service.py")))
            {
                return directory.FullName;
            }
            directory = directory.Parent;
        }

        return Directory.GetCurrentDirectory();
    }

    private static string? ResolveOption(string[] args, string name)
    {
        for (var index = 0; index < args.Length; index++)
        {
            var arg = args[index];
            if (string.Equals(arg, name, StringComparison.OrdinalIgnoreCase) && index + 1 < args.Length)
            {
                return args[index + 1];
            }

            var prefix = name + "=";
            if (arg.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                return arg[prefix.Length..];
            }
        }

        return null;
    }

    private static int RunConsole()
    {
        try
        {
            Directory.SetCurrentDirectory(_workspaceRoot);
            StartPythonChild();
            _childProcess?.WaitForExit();
            return _childProcess?.ExitCode ?? 0;
        }
        catch (Exception ex)
        {
            Log(ex.ToString());
            return 1;
        }
    }

    private static void ServiceMain(int argc, IntPtr argv)
    {
        _serviceStatusHandle = RegisterServiceCtrlHandlerEx(_serviceName, HandlerCallback, IntPtr.Zero);
        if (_serviceStatusHandle == IntPtr.Zero)
        {
            return;
        }

        SetStatus(ServiceStartPending, 0, 10_000);

        try
        {
            Directory.SetCurrentDirectory(_workspaceRoot);
            StartPythonChild();
            SetStatus(ServiceRunning, ServiceAcceptStop | ServiceAcceptShutdown, 0);

            while (!StopRequested.Wait(1_000))
            {
                if (_childProcess is not null && _childProcess.HasExited)
                {
                    Log($"Proceso Python termino con exit code {_childProcess.ExitCode}.");
                    break;
                }
            }

            SetStatus(ServiceStopPending, 0, 15_000);
            StopPythonChild();
            SetStatus(ServiceStopped, 0, 0);
        }
        catch (Exception ex)
        {
            Log(ex.ToString());
            SetStatus(ServiceStopped, 0, 0, 1);
        }
    }

    private static int HandlerEx(int control, int eventType, IntPtr eventData, IntPtr context)
    {
        if (control is ServiceControlStop or ServiceControlShutdown)
        {
            SetStatus(ServiceStopPending, 0, 15_000);
            StopRequested.Set();
        }

        return NoError;
    }

    private static void StartPythonChild()
    {
        Directory.CreateDirectory(RuntimeDir());
        DeleteStopFile();

        var pythonPath = Path.Combine(_workspaceRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(pythonPath))
        {
            pythonPath = "python.exe";
        }

        var scriptPath = Path.Combine(_workspaceRoot, "yarbis_service.py");
        if (!File.Exists(scriptPath))
        {
            throw new FileNotFoundException("No encontre yarbis_service.py.", scriptPath);
        }

        Log($"Iniciando proceso Python: {pythonPath} \"{scriptPath}\" --instance \"{_instanceId}\" --service-name \"{_serviceName}\"");
        var startInfo = new ProcessStartInfo
        {
            FileName = pythonPath,
            WorkingDirectory = _workspaceRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add(scriptPath);
        startInfo.ArgumentList.Add("--instance");
        startInfo.ArgumentList.Add(_instanceId);
        startInfo.ArgumentList.Add("--service-name");
        startInfo.ArgumentList.Add(_serviceName);
        startInfo.Environment["YARBIS_INSTANCE"] = _instanceId;
        startInfo.Environment["YARBIS_SERVICE_NAME"] = _serviceName;

        var ollamaKey = Environment.GetEnvironmentVariable("OLLAMA_API_KEY");
        if (!string.IsNullOrEmpty(ollamaKey))
        {
            startInfo.Environment["OLLAMA_API_KEY"] = ollamaKey;
        }

        _childProcess = Process.Start(startInfo);

        if (_childProcess is null)
        {
            throw new InvalidOperationException("No pude iniciar el proceso Python de Yarbis.");
        }
    }

    private static void StopPythonChild()
    {
        try
        {
            File.WriteAllText(StopFile(), "stop");
        }
        catch (Exception ex)
        {
            Log($"No pude escribir service.stop: {ex.Message}");
        }

        if (_childProcess is null)
        {
            return;
        }

        try
        {
            if (!_childProcess.WaitForExit(15_000))
            {
                Log("El proceso Python no se detuvo a tiempo. Terminando proceso hijo.");
                _childProcess.Kill(entireProcessTree: true);
                _childProcess.WaitForExit(5_000);
            }
        }
        catch (Exception ex)
        {
            Log($"Error deteniendo proceso Python: {ex}");
        }
    }

    private static void SetStatus(int state, int acceptedControls, int waitHint, int win32ExitCode = 0)
    {
        if (_serviceStatusHandle == IntPtr.Zero)
        {
            return;
        }

        var serviceStatus = new ServiceStatus
        {
            ServiceType = ServiceWin32OwnProcess,
            CurrentState = state,
            ControlsAccepted = acceptedControls,
            Win32ExitCode = win32ExitCode,
            ServiceSpecificExitCode = 0,
            CheckPoint = state is ServiceRunning or ServiceStopped ? 0 : _checkpoint++,
            WaitHint = waitHint,
        };
        SetServiceStatus(_serviceStatusHandle, ref serviceStatus);
    }

    private static string RuntimeDir()
    {
        if (string.Equals(_instanceId, DefaultInstanceId, StringComparison.OrdinalIgnoreCase))
        {
            return Path.Combine(_workspaceRoot, ".yarbis_runtime");
        }

        return Path.Combine(_workspaceRoot, ".yarbis_instances", _instanceId, ".yarbis_runtime");
    }

    private static string StopFile()
    {
        return Path.Combine(RuntimeDir(), "service.stop");
    }

    private static string LogFile()
    {
        return Path.Combine(RuntimeDir(), "service.log");
    }

    private static void DeleteStopFile()
    {
        try
        {
            var stopFile = StopFile();
            if (File.Exists(stopFile))
            {
                File.Delete(stopFile);
            }
        }
        catch
        {
            // Best effort only. Python also tolerates a stale stop file.
        }
    }

    private static void Log(string message)
    {
        try
        {
            Directory.CreateDirectory(RuntimeDir());
            File.AppendAllText(
                LogFile(),
                $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] [host] {message}{Environment.NewLine}"
            );
        }
        catch
        {
            // Logging must never prevent SCM status updates.
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ServiceTableEntry
    {
        [MarshalAs(UnmanagedType.LPWStr)]
        public string? ServiceName;
        public ServiceMainDelegate? ServiceMain;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ServiceStatus
    {
        public int ServiceType;
        public int CurrentState;
        public int ControlsAccepted;
        public int Win32ExitCode;
        public int ServiceSpecificExitCode;
        public int CheckPoint;
        public int WaitHint;
    }

    private delegate void ServiceMainDelegate(int argc, IntPtr argv);
    private delegate int HandlerExDelegate(int control, int eventType, IntPtr eventData, IntPtr context);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool StartServiceCtrlDispatcher([In] ServiceTableEntry[] serviceTable);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr RegisterServiceCtrlHandlerEx(
        string serviceName,
        HandlerExDelegate handlerProc,
        IntPtr context
    );

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool SetServiceStatus(IntPtr serviceStatusHandle, ref ServiceStatus serviceStatus);
}

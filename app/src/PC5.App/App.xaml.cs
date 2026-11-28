using Microsoft.UI.Xaml;
using Pc5.App.ViewModels;
using Pc5.Core.Configuration;
using Pc5.Core.Lifecycle;
using Pc5.Core.Logging;
using Pc5.Core.Services;

namespace Pc5.App;

/// <summary>
/// Application composition. Everything that could be decided in PC5.Core is decided there;
/// this class only builds the object graph, opens the log, and hands the window a view model.
///
/// A crash during initialization is written to the log file before it is rethrown, because an
/// application that dies on someone else's machine and leaves nothing behind cannot be
/// diagnosed by the person who has the machine.
/// </summary>
public sealed partial class App : Application
{
    public App()
    {
        InitializeComponent();
        UnhandledException += (_, args) =>
        {
            Host?.Log.Error("app.002", $"unhandled: {args.Message}");
            args.Handled = false;
        };
    }

    /// <summary>The composed host. Null before OnLaunched completes.</summary>
    public static HostComposition? Host { get; private set; }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        FileLog? log = null;
        var settingsPath = Pc5Paths.SettingsFile();
        try
        {
            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Pc5Paths.LogFile())!);
            log = new FileLog(Pc5Paths.LogFile(), LogLevel.Debug);
            var settings = new SettingsStore(System.IO.Path.GetDirectoryName(settingsPath), log).Load();
            Host = new HostComposition(log, settings);
            Host.Log.Info("app.001", $"PC5 launched; settings read from {settingsPath}");

            MainWindow = new MainWindow(Host);
            MainWindow.Activate();
        }
        catch (Exception exception)
        {
            log?.Error("app.003", $"initialization failed: {exception}");
            throw;
        }
    }
}

/// <summary>
/// The service boundary, assembled once. PC5.Core contracts are registered here and only
/// here, so a second component cannot quietly install a competing implementation. The
/// lifecycle state machine is shared by the backend and the view, so the window and the
/// engine cannot report two different truths about the same attempt.
/// </summary>
public sealed class HostComposition
{
    private LifecycleViewModel? viewModel;

    public HostComposition(FileLog log, Pc5Settings settings)
    {
        Log = log;
        Settings = settings;
        Machine = new ExecutionStateMachine();
        Services.Register<ILog>(log);
        Services.Register(settings);
        Services.Register(Machine);
        Services.Register<Pc5.Core.Execution.IExecutionEngine>(
            new Pc5.Core.Execution.Pc5TestBackend(Machine, log));
        Services.Register<Pc5.Core.Diagnostics.IHostCapabilityProbe>(new Platform.WindowsHostProbe());
    }

    public ServiceRegistry Services { get; } = new();

    public FileLog Log { get; }

    public Pc5Settings Settings { get; }

    public ExecutionStateMachine Machine { get; }

    /// <summary>Created on first use, after every service it resolves has been registered.</summary>
    public LifecycleViewModel ViewModel => viewModel ??= new LifecycleViewModel(this);
}

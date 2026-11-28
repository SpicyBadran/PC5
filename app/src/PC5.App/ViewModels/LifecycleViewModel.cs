using Pc5.Core.Configuration;
using Pc5.Core.Diagnostics;
using Pc5.Core.Errors;
using Pc5.Core.Execution;
using Pc5.Core.Firmware;
using Pc5.Core.Lifecycle;
using Pc5.Core.Versioning;

namespace Pc5.App.ViewModels;

/// <summary>
/// Everything the window shows, computed outside the window. This is the one place where
/// PC5's lifecycle, host report and status string meet, which keeps the claim surface in a
/// single file a reviewer can read: the line at the bottom is what this build may assert, and
/// it comes from <see cref="ExecutionStatus.AllowableClaim"/>, never from the UI.
/// </summary>
public sealed class LifecycleViewModel
{
    private readonly HostComposition host;

    public LifecycleViewModel(HostComposition host)
    {
        this.host = host;
        host.Machine.Changed += _ => Changed?.Invoke();
    }

    /// <summary>The view raises this instead of polling; the values still come from the core.</summary>
    public event Action? Changed;

    public Pc5Settings Settings => host.Settings;

    public string Title => BuildInfo.Describe();

    public ExecutionState State => host.Machine.Current;

    public IReadOnlyList<StateChange> History => host.Machine.History;

    public FirmwareArtifactReport Firmware => FirmwareArtifactProbe.Describe(host.Settings.FirmwarePath);

    public async Task<string> RefreshCapabilitiesAsync()
    {
        var probe = host.Services.Resolve<IHostCapabilityProbe>();
        var capabilities = await probe.DetectAsync();
        var directory = Pc5Paths.ReportsDirectory();
        System.IO.Directory.CreateDirectory(directory);
        var path = System.IO.Path.Combine(directory, $"host-capabilities-{DateTime.UtcNow:yyyyMMdd-HHmmss}.txt");
        await File.WriteAllTextAsync(path, HostCapabilityReport.ToText(capabilities));
        host.Log.Info("ui.001", $"host capability report written to {path}");
        return await File.ReadAllTextAsync(path);
    }

    public async Task<string> StartTestBackendAsync()
    {
        var engine = host.Services.Resolve<IExecutionEngine>();
        if (host.Machine.Current is ExecutionState.Starting or ExecutionState.Running or ExecutionState.Paused)
        {
            throw new Pc5Exception(
                Pc5ErrorCategory.InvalidTransition,
                $"a lifecycle is already in progress ({host.Machine.Current})",
                "stop the running lifecycle before starting another; PC5 runs one lifecycle at a time");
        }

        var configuration = new ExecutionConfiguration
        {
            BackendId = engine.Id,
            GuestMemoryMiB = Settings.GuestMemoryMiB,
            GuestProcessors = Settings.GuestProcessors,
            StorageDirectory = Settings.StorageDirectory,
            FirmwarePath = Settings.FirmwarePath,
            Label = "interactive test workload from PC5.exe",
        };

        host.Log.Info("ui.002", $"start requested on backend {engine.Id} (label: {configuration.Label})");
        await engine.InitializeAsync(configuration);
        await engine.StartAsync();
        return (await engine.GetStatusAsync()).AllowableClaim();
    }

    public async Task<string> StopTestBackendAsync()
    {
        var engine = host.Services.Resolve<IExecutionEngine>();
        await engine.StopAsync();
        host.Log.Info("ui.003", "lifecycle stopped; no guest of any kind is running");
        return "stopped. no guest of any kind is running";
    }

    public string LogTail(int lines = 40) => host.Log.Tail(lines);
}

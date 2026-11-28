using System.Text.Json;
using Pc5.Core.Configuration;
using Pc5.Core.Errors;
using Pc5.Core.Logging;
using Xunit;

namespace Pc5.Core.Tests;

public class SettingsAndLogTests : IDisposable
{
    private readonly string directory;

    public SettingsAndLogTests() =>
        directory = Path.Combine(Path.GetTempPath(), "pc5-tests-" + Guid.NewGuid().ToString("N"));

    [Fact]
    public void AbsentSettingsYieldDefaultsThatAreValid()
    {
        var settings = new SettingsStore(directory).Load();

        Assert.Equal("test-backend", settings.Backend);
        Assert.True(settings.IsValid);
        Assert.Equal(Pc5Settings.CurrentVersion, settings.Version);
    }

    [Fact]
    public void SettingsSurviveAWriteAndReadWithoutLosingAnything()
    {
        var store = new SettingsStore(directory);
        var written = new Pc5Settings
        {
            Backend = "test-backend",
            GuestMemoryMiB = 4096,
            GuestProcessors = 4,
            LogLevel = "Debug",
            FirmwarePath = "/secure/PS5UPDATE.PUP",
        };

        store.Save(written);
        var read = store.Load();

        Assert.Equal(written, read);
        Assert.Equal("/secure/PS5UPDATE.PUP", read.FirmwarePath);
    }

    [Fact]
    public void InvalidSettingsAreRefusedAtTheBoundary()
    {
        var store = new SettingsStore(directory);
        var error = Assert.Throws<Pc5Exception>(() => store.Save(new Pc5Settings { GuestMemoryMiB = 4 }));

        Assert.Equal(Pc5ErrorCategory.Configuration, error.Category);
        Assert.False(File.Exists(store.FilePath));
    }

    [Fact]
    public void AHostWithFewerProcessorsThanConfiguredIsReportedNotSilentlyAccepted()
    {
        var settings = new Pc5Settings { GuestProcessors = 1 + (Environment.ProcessorCount * 4) };
        Assert.Contains(settings.Problems(), problem => problem.Contains("guestProcessors", StringComparison.Ordinal));
    }

    [Fact]
    public void AnUnreadableSettingsFileIsQuarantinedAndReported()
    {
        Directory.CreateDirectory(directory);
        File.WriteAllText(Path.Combine(directory, "settings.json"), "{ this is not json");
        var log = new MemoryLog();

        var store = new SettingsStore(directory, log);
        var error = Assert.Throws<Pc5Exception>(() => store.Load());

        Assert.Equal(Pc5ErrorCategory.Configuration, error.Category);
        Assert.True(File.Exists(Path.Combine(directory, "settings.json.bad")));
        Assert.True(log.Contains("defaults used"));
    }

    [Fact]
    public void ARelativeStorageDirectoryIsRejectedBecauseGuestStorageMustBeIsolated()
    {
        var settings = new Pc5Settings { StorageDirectory = "guest-disks" };
        Assert.Contains(settings.Problems(), problem => problem.Contains("absolute path", StringComparison.Ordinal));
    }

    [Fact]
    public void TheLevelFilterCountsWhatItRefused()
    {
        var path = Path.Combine(directory, "logs", "pc5.log");
        using (var log = new FileLog(path, LogLevel.Warn))
        {
            log.Debug("t.001", "below the filter");
            log.Warn("t.002", "kept");
            log.Error("t.003", "kept too");
            Assert.Equal(1, log.Suppressed);
            Assert.Equal(2, log.Tail().Split('\n').Where(line => line.Length > 0).Count());
        }

        var text = File.ReadAllText(path);
        Assert.Contains("[t.002]", text, StringComparison.Ordinal);
        Assert.DoesNotContain("[t.001]", text, StringComparison.Ordinal);
    }

    [Fact]
    public void EveryLogLineCarriesAnEventId()
    {
        using var log = new FileLog(Path.Combine(directory, "id.log"), LogLevel.Trace);
        Assert.Throws<ArgumentException>(() => log.Write(LogLevel.Info, " ", "no id"));
    }

    [Fact]
    public void AWriteFailureIsToldToTheCallerInsteadOfVanishing()
    {
        // A parent path that exists as a *file* cannot be created as a directory, so the
        // writer really does fail: the test proves the callback fires, not that a path looked odd.
        Directory.CreateDirectory(directory);
        var blocker = Path.Combine(directory, "blocker");
        File.WriteAllText(blocker, "not a directory");
        var reported = new List<string>();
        var path = Path.Combine(blocker, "pc5.log");
        using (var log = new FileLog(path, LogLevel.Info, reported.Add))
        {
            log.Info("t.010", "one line");
        }

        Assert.Single(reported);
        Assert.Contains("log write", reported[0], StringComparison.Ordinal);
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(directory))
            {
                System.IO.Directory.Delete(directory, recursive: true);
            }
        }
        catch (IOException)
        {
            // A leftover temp directory is not a test failure worth hiding.
        }
    }
}

/// <summary>Two seams PC5 must not get wrong: unknown is not false, and a duplicate service is a bug.</summary>
public class CapabilityAndServiceTests
{
    [Fact]
    public async Task UnknownFindingsAreRenderedAsUnknownAndListed()
    {
        var capabilities = await new Pc5.Core.Diagnostics.RuntimeHostProbe().DetectAsync();

        Assert.Contains("unknown", Pc5.Core.Diagnostics.HostCapabilityReport.ToText(capabilities), StringComparison.Ordinal);
        Assert.Contains(capabilities.Unknowns, name => name == "cpu-features");
        Assert.False(capabilities.FullyDetermined);
        Assert.Contains("does not", Pc5.Core.Diagnostics.HostCapabilityReport.ToText(capabilities), StringComparison.Ordinal);
    }

    [Fact]
    public void TheReportNeverClaimsGuestExecution()
    {
        var capabilities = new Pc5.Core.Diagnostics.HostCapabilities
        {
            OsDescription = "Test",
            ProcessArchitecture = "X64",
            LogicalProcessors = 2,
            TotalMemoryMiB = 1024,
            Findings = new[]
            {
                new Pc5.Core.Diagnostics.CapabilityFinding
                {
                    Name = "probe",
                    Supported = false,
                    Evidence = "a unit test",
                    Meaning = "nothing",
                    NotProven = "PS5 execution",
                },
            },
        };

        var text = Pc5.Core.Diagnostics.HostCapabilityReport.ToText(capabilities);
        Assert.Contains("says nothing about whether any", text, StringComparison.Ordinal);
        Assert.Contains("does not   : PS5 execution", text, StringComparison.Ordinal);
        Assert.Contains("\"supported\": false", Pc5.Core.Diagnostics.HostCapabilityReport.ToJson(capabilities).Replace("\r\n", "\n"), StringComparison.Ordinal);
    }

    [Fact]
    public void OneContractHasExactlyOneOwner()
    {
        var registry = new Pc5.Core.Services.ServiceRegistry();
        registry.Register<Pc5.Core.Logging.ILog>(new MemoryLog());

        var duplicate = Assert.Throws<Pc5Exception>(() => registry.Register<Pc5.Core.Logging.ILog>(new MemoryLog()));
        Assert.Equal(Pc5ErrorCategory.Internal, duplicate.Category);
        Assert.True(registry.TryResolve<Pc5.Core.Logging.ILog>(out var found));
        Assert.NotNull(found);
        Assert.False(registry.TryResolve<Pc5.Core.Execution.IExecutionEngine>(out var missing));
        Assert.Null(missing);
        Assert.Throws<Pc5Exception>(() => registry.Resolve<Pc5.Core.Execution.IExecutionEngine>());
    }
}

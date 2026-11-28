using Pc5.Core.Errors;
using Pc5.Core.Execution;
using Pc5.Core.Lifecycle;
using Pc5.Core.Logging;
using Pc5.Core.Versioning;
using Xunit;

namespace Pc5.Core.Tests;

public class TestBackendLifecycleTests
{
    private static ExecutionConfiguration Config(string label = "unit-test-workload") => new()
    {
        BackendId = "test-backend",
        GuestMemoryMiB = 256,
        GuestProcessors = 1,
        Label = label,
    };

    [Fact]
    public async Task AFullLifecycleEndsStoppedAndNeverClaimsAGuest()
    {
        var machine = new ExecutionStateMachine();
        var log = new MemoryLog();
        var backend = new Pc5TestBackend(machine, log, stepDelay: TimeSpan.Zero);

        await backend.InitializeAsync(Config());
        await backend.StartAsync();
        var running = await backend.GetStatusAsync();
        await backend.StopAsync();

        Assert.Equal(ExecutionState.Stopped, machine.Current);
        Assert.Equal(GuestKind.TestWorkload, running.Guest);
        Assert.Equal(EvidenceLevel.GenericGuest, running.Level);
        Assert.Contains("not PS5", running.AllowableClaim(), StringComparison.Ordinal);
        Assert.Contains("not PS5", backend.DisplayName, StringComparison.Ordinal);
        Assert.True(log.Contains("test workload running"));
    }

    [Fact]
    public async Task AStoppedBackendReportsNoGuestAtAll()
    {
        var backend = new Pc5TestBackend(new ExecutionStateMachine(), null, TimeSpan.Zero);
        await backend.InitializeAsync(Config());
        var status = await backend.GetStatusAsync();

        Assert.Equal(GuestKind.None, status.Guest);
        Assert.Equal(EvidenceLevel.ApplicationBuilds, status.Level);
        Assert.Equal("the PC5 application exists; no guest has been started", status.AllowableClaim());
    }

    [Fact]
    public async Task StartingBeforeInitializingIsRefusedAndSaysWhatToCall()
    {
        var backend = new Pc5TestBackend(new ExecutionStateMachine(), null, TimeSpan.Zero);
        var error = await Assert.ThrowsAsync<Pc5Exception>(() => backend.StartAsync());

        Assert.Equal(Pc5ErrorCategory.InvalidTransition, error.Category);
        Assert.Contains("InitializeAsync", error.NextStep, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AnInjectedFailureAtStartLeavesTheMachineFailedNotStopped()
    {
        var machine = new ExecutionStateMachine();
        var backend = new Pc5TestBackend(machine, null, TimeSpan.Zero, TestFailurePoint.Start);
        await backend.InitializeAsync(Config());

        await Assert.ThrowsAsync<Pc5Exception>(() => backend.StartAsync());

        Assert.Equal(ExecutionState.Failed, machine.Current);
        Assert.DoesNotContain(machine.History, change => change.To == ExecutionState.Stopped);
    }

    [Fact]
    public async Task CancellationDuringStartFailsTheLifecycleAndPropagates()
    {
        var machine = new ExecutionStateMachine();
        var backend = new Pc5TestBackend(machine, null, TimeSpan.FromMilliseconds(50));
        await backend.InitializeAsync(Config());
        using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(2));

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => backend.StartAsync(timeout.Token));
        Assert.Equal(ExecutionState.Failed, machine.Current);
    }

    [Fact]
    public async Task PauseAndResumeRequireTheHandleThePauseReturned()
    {
        var backend = new Pc5TestBackend(new ExecutionStateMachine(), null, TimeSpan.Zero);
        await backend.InitializeAsync(Config());
        await backend.StartAsync();

        var handle = await backend.PauseAsync();
        var status = await backend.GetStatusAsync();
        Assert.Equal(ExecutionState.Paused, status.State);

        await Assert.ThrowsAsync<Pc5Exception>(() => backend.ResumeAsync(new PauseHandle(Guid.NewGuid())));
        await backend.ResumeAsync(handle);
        Assert.Equal(ExecutionState.Running, (await backend.GetStatusAsync()).State);
    }

    [Fact]
    public async Task TheWorkloadOnlyAdvancesWhileRunning()
    {
        var backend = new Pc5TestBackend(new ExecutionStateMachine(), null, TimeSpan.Zero);
        await backend.InitializeAsync(Config());

        Assert.Throws<Pc5Exception>(() => backend.AdvanceWorkload());
        await backend.StartAsync();
        Assert.Equal(3, backend.AdvanceWorkload(3));
        await backend.StopAsync();
        Assert.Throws<Pc5Exception>(() => backend.AdvanceWorkload());
    }

    [Fact]
    public async Task TheBackendDeclaresItsOwnCeilingBeforeAnyoneAsks()
    {
        var backend = new Pc5TestBackend(new ExecutionStateMachine(), null, TimeSpan.Zero);
        var support = await backend.ProbeAsync();

        Assert.True(support.Supported);
        Assert.Equal(EvidenceLevel.GenericGuest, support.Ceiling);
        Assert.Equal(EvidenceLevel.GenericGuest, backend.MaximumEvidenceLevel);
        Assert.True(backend.MaximumEvidenceLevel < EvidenceLevel.GenuineOsBoots);
    }

    [Fact]
    public void TheBuildCarriesAPublicCeilingAndATruthStatement()
    {
        Assert.Equal(EvidenceLevel.ApplicationBuilds, BuildInfo.BuildEvidenceCeiling);
        Assert.Contains("has not been demonstrated", BuildInfo.TruthStatement, StringComparison.Ordinal);
        Assert.StartsWith("PC5 ", BuildInfo.Describe(), StringComparison.Ordinal);
    }
}

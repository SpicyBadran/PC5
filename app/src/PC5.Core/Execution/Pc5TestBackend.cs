using Pc5.Core.Diagnostics;
using Pc5.Core.Errors;
using Pc5.Core.Lifecycle;
using Pc5.Core.Logging;

namespace Pc5.Core.Execution;

/// <summary>
/// The only execution backend PC5 ships today, and it is named so that it cannot be
/// mistaken for the objective: it runs a PC5-authored workload that exercises the
/// lifecycle, the state machine, logging, cancellation and teardown.
///
/// It reports <see cref="EvidenceLevel.GenericGuest"/> at best and
/// <see cref="GuestKind.TestWorkload"/> always. It has no code path that can enter a state
/// claiming a PlayStation, and its status string says "not PS5 execution" every time.
/// </summary>
public sealed class Pc5TestBackend : IExecutionEngine
{
    private readonly ExecutionStateMachine machine;
    private readonly ILog? log;
    private readonly TimeSpan stepDelay;
    private readonly TestFailurePoint failAt;
    private ExecutionConfiguration? configuration;
    /// <summary>The outstanding pause, or none. Typed as the handle itself and not as a
    /// <see cref="Guid"/>: the first hosted compile (run 461090283704's successor) rejected
    /// <c>pause.Value != handle</c> because a bare <c>Guid</c> and a <c>PauseHandle</c> are not
    /// comparable, and they should not be -- the handle is the thing a caller must return, so
    /// the field has to hold that same type for the refusal in <see cref="ResumeAsync"/> to mean
    /// "not the handle I gave you" rather than "not the GUID I happened to hash".</summary>
    private PauseHandle? pause;
    private int ticks;

    public Pc5TestBackend(
        ExecutionStateMachine? machine = null,
        ILog? log = null,
        TimeSpan? stepDelay = null,
        TestFailurePoint failAt = TestFailurePoint.None)
    {
        this.machine = machine ?? new ExecutionStateMachine();
        this.log = log;
        this.stepDelay = stepDelay ?? TimeSpan.FromMilliseconds(10);
        this.failAt = failAt;
    }

    public string Id => "test-backend";

    public string DisplayName => "PC5 test backend (not PS5)";

    public EvidenceLevel MaximumEvidenceLevel => EvidenceLevel.GenericGuest;

    /// <summary>The lifecycle the backend drove, exposed so a test can assert the exact edges.</summary>
    public ExecutionState State => machine.Current;

    public IReadOnlyList<StateChange> History => machine.History;

    public Task<HostSupportResult> ProbeAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(new HostSupportResult(
            Supported: true,
            Reason: "the test backend needs no host feature beyond a .NET process",
            Ceiling: EvidenceLevel.GenericGuest));

    public async Task InitializeAsync(ExecutionConfiguration configuration, CancellationToken cancellationToken = default)
    {
        this.configuration = configuration ?? throw new ArgumentNullException(nameof(configuration));
        machine.MoveTo(ExecutionState.Initializing, $"test backend {Id} initializing for '{configuration.Label}'");
        await Step(cancellationToken).ConfigureAwait(false);
        if (failAt == TestFailurePoint.Initialize)
        {
            machine.MoveTo(ExecutionState.Failed, "injected failure at initialize");
            throw new Pc5Exception(
                Pc5ErrorCategory.BackendUnavailable,
                "the test backend was told to fail during initialization",
                "clear the injected failure point and retry");
        }

        machine.MoveTo(ExecutionState.Ready, "host-side preparation complete; no guest has run yet");
        log?.Info("exec.001", $"initialized backend={Id} label={configuration.Label}");
    }

    public async Task StartAsync(CancellationToken cancellationToken = default)
    {
        RequireConfiguration();
        machine.MoveTo(ExecutionState.Starting, "start requested");
        await Step(cancellationToken).ConfigureAwait(false);
        if (failAt == TestFailurePoint.Start)
        {
            machine.MoveTo(ExecutionState.Failed, "injected failure at start");
            throw new Pc5Exception(
                Pc5ErrorCategory.BackendUnavailable,
                "the test backend was told to fail during start",
                "read the log for the injected failure point; this is a harness failure, not a host failure");
        }

        machine.MoveTo(ExecutionState.Running,
            "test workload running; this is not PS5 execution and must not be reported as such");
        log?.Info("exec.002", "test workload running (GuestKind=TestWorkload, not PS5)");
    }

    public async Task<PauseHandle> PauseAsync(CancellationToken cancellationToken = default)
    {
        await Step(cancellationToken).ConfigureAwait(false);
        var handle = new PauseHandle(Guid.NewGuid());
        pause = handle;
        machine.MoveTo(ExecutionState.Paused, $"pause {handle.Id}");
        return handle;
    }

    public async Task ResumeAsync(PauseHandle handle, CancellationToken cancellationToken = default)
    {
        if (pause is null || pause.Value != handle)
        {
            throw new Pc5Exception(
                Pc5ErrorCategory.InvalidTransition,
                $"resume refused: {handle.Id} is not the outstanding pause",
                "pause first, then pass the handle PauseAsync returned");
        }

        await Step(cancellationToken).ConfigureAwait(false);
        pause = null;
        machine.MoveTo(ExecutionState.Running, $"resumed from pause {handle.Id}");
    }

    public async Task StopAsync(CancellationToken cancellationToken = default)
    {
        await Step(cancellationToken).ConfigureAwait(false);
        machine.MoveTo(ExecutionState.Stopping, "stop requested");
        ticks = 0;
        machine.MoveTo(ExecutionState.Stopped, "test workload torn down cleanly");
        log?.Info("exec.003", "test backend stopped");
    }

    public Task<ExecutionStatus> GetStatusAsync(CancellationToken cancellationToken = default)
    {
        var guest = machine.Current == ExecutionState.Running || machine.Current == ExecutionState.Paused
            ? GuestKind.TestWorkload
            : GuestKind.None;
        var level = guest == GuestKind.TestWorkload ? EvidenceLevel.GenericGuest : EvidenceLevel.ApplicationBuilds;
        return Task.FromResult(new ExecutionStatus
        {
            State = machine.Current,
            Guest = guest,
            Level = level,
            BackendId = Id,
            Detail = $"test backend '{DisplayName}': workload ticks={ticks}; guest kind {guest}",
        });
    }

    /// <summary>A workload step: the only "computation" this backend performs, on purpose.</summary>
    public int AdvanceWorkload(int amount = 1)
    {
        if (machine.Current != ExecutionState.Running)
        {
            throw new Pc5Exception(
                Pc5ErrorCategory.InvalidTransition,
                $"workload step refused in state {machine.Current}",
                "the test workload only advances while the lifecycle reports Running");
        }

        ticks += amount;
        return ticks;
    }

    private void RequireConfiguration()
    {
        if (configuration is null)
        {
            throw new Pc5Exception(
                Pc5ErrorCategory.InvalidTransition,
                "start requested before initialize",
                "call InitializeAsync before StartAsync; PC5 never starts a lifecycle it has not configured");
        }
    }

    private async Task Step(CancellationToken cancellationToken)
    {
        if (stepDelay <= TimeSpan.Zero)
        {
            cancellationToken.ThrowIfCancellationRequested();
            return;
        }

        try
        {
            await Task.Delay(stepDelay, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            machine.MoveTo(ExecutionState.Failed, "cancelled by request while the backend was working");
            log?.Warn("exec.004", "lifecycle cancelled");
            throw;
        }
    }
}

/// <summary>Where the test backend injects a failure, so failure paths are exercised on purpose.</summary>
public enum TestFailurePoint { None, Initialize, Start }

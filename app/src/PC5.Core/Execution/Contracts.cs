using Pc5.Core.Lifecycle;

namespace Pc5.Core.Execution;

/// <summary>
/// What a piece of evidence proves, as PC5 grades it. This ladder is the project's
/// defence against its own most likely failure: reporting infrastructure progress as
/// genuine guest execution.
/// </summary>
public enum EvidenceLevel
{
    /// <summary>E0 - research and design only. Proves understanding, nothing else.</summary>
    Research,

    /// <summary>E1 - the Windows application builds. Proves the host application exists.</summary>
    ApplicationBuilds,

    /// <summary>E2 - a generic guest boots. Proves the execution infrastructure works.</summary>
    GenericGuest,

    /// <summary>E3 - a PS5-related component is identified and validated. Compatibility progress.</summary>
    IdentifiedComponent,

    /// <summary>E4 - authorised PS5 system-software components execute. Partial genuine execution.</summary>
    GenuineComponents,

    /// <summary>E5 - the genuine PS5 operating system boots. The core objective.</summary>
    GenuineOsBoots,

    /// <summary>E6 - the genuine guest operates inside PC5 with validated devices and services.</summary>
    IntegratedGuest,
}

/// <summary>What is actually running under a backend. The distinction is not cosmetic.</summary>
public enum GuestKind
{
    /// <summary>Nothing is running.</summary>
    None,

    /// <summary>A PC5-authored workload, used to exercise the lifecycle. Never a PS5 claim.</summary>
    TestWorkload,

    /// <summary>A general-purpose guest OS, e.g. a stock Linux or Windows image.</summary>
    GenericGuest,

    /// <summary>A component identified as PS5 system software, run in isolation.</summary>
    Ps5Component,

    /// <summary>The genuine PS5 operating system, as a guest.</summary>
    Ps5SystemSoftware,
}

/// <summary>How a caller asks for a lifecycle to be configured.</summary>
public sealed record ExecutionConfiguration
{
    public required string BackendId { get; init; }
    public int GuestMemoryMiB { get; init; } = 512;
    public int GuestProcessors { get; init; } = 2;
    public string? StorageDirectory { get; init; }
    public string? FirmwarePath { get; init; }

    /// <summary>Free text naming the workload, so a log can be read years later.</summary>
    public string Label { get; init; } = "unlabelled";
}

/// <summary>What a backend says about itself at a moment in time.</summary>
public sealed record ExecutionStatus
{
    public required ExecutionState State { get; init; }
    public required GuestKind Guest { get; init; }
    public required EvidenceLevel Level { get; init; }
    public required string BackendId { get; init; }
    public string Detail { get; init; } = string.Empty;
    public DateTimeOffset AtUtc { get; init; } = DateTimeOffset.UtcNow;

    /// <summary>
    /// The claim this status is allowed to support, and nothing wider. A generic VM at E2
    /// cannot be described as PS5 execution, and the string the application prints says so.
    /// </summary>
    public string AllowableClaim() => Level switch
    {
        EvidenceLevel.Research => "design only; nothing has run",
        EvidenceLevel.ApplicationBuilds => "the PC5 application exists; no guest has been started",
        EvidenceLevel.GenericGuest => $"a generic guest ran under {BackendId}; this is not PS5 execution",
        EvidenceLevel.IdentifiedComponent => "a component was identified; it has not been shown to be a booting OS",
        EvidenceLevel.GenuineComponents => "authorised PS5 components executed; the operating system did not necessarily boot",
        EvidenceLevel.GenuineOsBoots => "the genuine PS5 operating system booted",
        EvidenceLevel.IntegratedGuest => "the genuine guest operates inside PC5 with devices and services validated",
        _ => "uncategorised evidence; treat as unproven",
    };
}

/// <summary>
/// The seam between the PC5 application and whatever executes a guest.
///
/// Deliberately narrow: the application may drive a lifecycle and read status, and nothing
/// else. Anything that would let a backend assert "PS5 running" without evidence is absent
/// by design.
/// </summary>
public interface IExecutionEngine
{
    string Id { get; }

    /// <summary>Human-facing name, printed in the UI. It must not overstate what runs.</summary>
    string DisplayName { get; }

    /// <summary>The highest evidence level this backend can produce, declared up front.</summary>
    EvidenceLevel MaximumEvidenceLevel { get; }

    Task<HostSupportResult> ProbeAsync(CancellationToken cancellationToken = default);

    Task InitializeAsync(ExecutionConfiguration configuration, CancellationToken cancellationToken = default);

    Task StartAsync(CancellationToken cancellationToken = default);

    Task<PauseHandle> PauseAsync(CancellationToken cancellationToken = default);

    Task ResumeAsync(PauseHandle handle, CancellationToken cancellationToken = default);

    Task StopAsync(CancellationToken cancellationToken = default);

    Task<ExecutionStatus> GetStatusAsync(CancellationToken cancellationToken = default);
}

/// <summary>An opaque token for a pause, so a resume cannot be issued blindly.</summary>
public readonly record struct PauseHandle(Guid Id);

/// <summary>Whether this host can run this backend at all, with the reason.</summary>
public sealed record HostSupportResult(bool Supported, string Reason, EvidenceLevel Ceiling);

namespace Pc5.Core.Diagnostics;

/// <summary>
/// A host capability as PC5 is allowed to know it: three-valued, with the evidence that
/// produced it and the thing it does NOT prove.
///
/// The distinction between <see cref="Unknown"/> and a definite false is the whole point
/// of this type. A probe that could not run must not become "not supported", and a probe
/// that merely found a file must not become "usable by PC5".
/// </summary>
public sealed record CapabilityFinding
{
    public required string Name { get; init; }

    /// <summary>null means "not established here", which is not the same as false.</summary>
    public bool? Supported { get; init; }

    /// <summary>How the value was obtained, in terms a reviewer can repeat.</summary>
    public required string Evidence { get; init; }

    /// <summary>What the value means for PC5, stated narrowly.</summary>
    public required string Meaning { get; init; }

    /// <summary>What this finding does not establish. Always filled in; never left implied.</summary>
    public required string NotProven { get; init; }

    public string ValueText => Supported switch
    {
        true => "yes",
        false => "no",
        null => "unknown",
    };

    public override string ToString() => $"{Name}={ValueText}";
}

/// <summary>
/// What PC5's host can offer, as a reportable object. Built by a probe (the Windows app
/// supplies one); rendered by <see cref="HostCapabilityReport"/>; never assembled by hand
/// in the UI layer.
/// </summary>
public sealed record HostCapabilities
{
    public required string OsDescription { get; init; }
    public required string ProcessArchitecture { get; init; }
    public required int LogicalProcessors { get; init; }

    /// <summary>Managed memory budget reported by the runtime, in mebibytes.</summary>
    public required long TotalMemoryMiB { get; init; }

    /// <summary>Findings in the order the application should present them.</summary>
    public required IReadOnlyList<CapabilityFinding> Findings { get; init; }

    public DateTimeOffset ProbedAtUtc { get; init; } = DateTimeOffset.UtcNow;

    /// <summary>Any finding this build would need in order to be definite, and is not.</summary>
    public IReadOnlyList<string> Unknowns =>
        Findings.Where(f => f.Supported is null).Select(f => f.Name).ToArray();

    /// <summary>True only when every finding is definite. Absence of unknowns is not proof of readiness.</summary>
    public bool FullyDetermined => Findings.All(f => f.Supported is not null);
}

/// <summary>A probe supplies capability findings for one host.</summary>
public interface IHostCapabilityProbe
{
    string Id { get; }

    Task<HostCapabilities> DetectAsync(CancellationToken cancellationToken = default);
}

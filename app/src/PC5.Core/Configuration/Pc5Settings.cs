namespace Pc5.Core.Configuration;

/// <summary>
/// PC5's settings, versioned on disk.
///
/// Two rules, both load-bearing: a settings file never stores a path to proprietary Sony
/// firmware as a claim that the firmware exists (it stores a path, and the path is checked
/// at use time and reported honestly), and an unreadable file is not silently replaced --
/// it is reported, backed up, and the defaults are used so the application still starts.
/// </summary>
public sealed record Pc5Settings
{
    public const int CurrentVersion = 1;

    /// <summary>Schema version, so a future migration can tell what it is reading.</summary>
    public int Version { get; init; } = CurrentVersion;

    /// <summary>Execution backend id. "test-backend" is the only value this build ships.</summary>
    public string Backend { get; init; } = "test-backend";

    /// <summary>Guest memory request in mebibytes. A request, not a promise about contiguity.</summary>
    public int GuestMemoryMiB { get; init; } = 512;

    /// <summary>Guest vCPU count, bounded by validation below.</summary>
    public int GuestProcessors { get; init; } = 2;

    /// <summary>Directory holding guest-visible virtual disks, isolated from host system files.</summary>
    public string? StorageDirectory { get; init; }

    /// <summary>Optional path to a firmware package for integrity verification. Never its contents.</summary>
    public string? FirmwarePath { get; init; }

    /// <summary>Log verbosity: Trace, Debug, Info, Warn or Error.</summary>
    public string LogLevel { get; init; } = "Info";

    /// <summary>Refuse nonsense at the boundary instead of passing it to a backend.</summary>
    public IReadOnlyList<string> Problems()
    {
        var problems = new List<string>();
        if (Version != CurrentVersion)
        {
            problems.Add($"version is {Version}, this build writes {CurrentVersion}");
        }

        if (GuestMemoryMiB < 128 || GuestMemoryMiB > 32768)
        {
            problems.Add($"guestMemoryMiB {GuestMemoryMiB} is outside the 128..32768 range this build validates");
        }

        if (GuestProcessors < 1 || GuestProcessors > Math.Max(1, Environment.ProcessorCount))
        {
            problems.Add($"guestProcessors {GuestProcessors} is outside 1..{Math.Max(1, Environment.ProcessorCount)} on this host");
        }

        if (string.IsNullOrWhiteSpace(Backend))
        {
            problems.Add("backend must name an execution backend");
        }

        if (!Enum.TryParse<Logging.LogLevel>(LogLevel, ignoreCase: true, out _))
        {
            problems.Add($"logLevel '{LogLevel}' is not one of Trace, Debug, Info, Warn, Error");
        }

        if (StorageDirectory is { Length: > 0 } directory && PathChecks.IsRelative(directory))
        {
            problems.Add($"storageDirectory {directory} is not an absolute path");
        }

        return problems;
    }

    public bool IsValid => Problems().Count == 0;
}

/// <summary>Path rules PC5 applies to configured locations.</summary>
internal static class PathChecks
{
    /// <summary>True when a configured directory is relative, which PC5 refuses for guest storage.</summary>
    public static bool IsRelative(string candidate) =>
        !System.IO.Path.IsPathFullyQualified(candidate);
}

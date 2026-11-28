using System.Reflection;
using Pc5.Core.Execution;

namespace Pc5.Core.Versioning;

/// <summary>
/// What this build is, and the most it is allowed to claim.
///
/// The ceiling is a property of the build, not of the machine or the settings: a release
/// build cannot present an evidence level it has not earned, so the constant lives here
/// where the UI, the log and the report all read the same value.
/// </summary>
public static class BuildInfo
{
    public const string ProductName = "PC5";

    /// <summary>The highest claim any build of this revision may make about guest execution.</summary>
    public const EvidenceLevel BuildEvidenceCeiling = EvidenceLevel.ApplicationBuilds;

    /// <summary>Printed in the application's status area, always, next to the version.</summary>
    public const string TruthStatement =
        "genuine PlayStation 5 operating-system execution has not been demonstrated";

    public static string Version =>
        typeof(BuildInfo).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
        ?? typeof(BuildInfo).Assembly.GetName().Version?.ToString(3)
        ?? "unversioned";

    public static string Describe() =>
        $"{ProductName} {Version} - evidence ceiling: {BuildEvidenceCeiling}. {TruthStatement}.";
}

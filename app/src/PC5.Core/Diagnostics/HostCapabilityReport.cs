using System.Text;

namespace Pc5.Core.Diagnostics;

/// <summary>
/// The machine-readable and human-readable renderings of a <see cref="HostCapabilities"/>.
///
/// This is the object PC5.exe writes to disk on demand, and the file a reviewer is asked to
/// attach to a report. It is deliberately explicit about the word "unknown" and about the
/// limits of each finding, because a diagnostics report that reads like a certification is
/// worse than none: it gets quoted.
/// </summary>
public static class HostCapabilityReport
{
    public static string ToText(HostCapabilities capabilities)
    {
        var builder = new StringBuilder();
        builder.AppendLine("PC5 host capability report");
        builder.AppendLine("--------------------------");
        builder.AppendLine($"generated        : {capabilities.ProbedAtUtc:O}");
        builder.AppendLine($"probe set      : see each finding's evidence line");
        builder.AppendLine($"operating system : {capabilities.OsDescription}");
        builder.AppendLine($"architecture     : {capabilities.ProcessArchitecture}");
        builder.AppendLine($"logical CPUs     : {capabilities.LogicalProcessors}");
        builder.AppendLine($"memory budget    : {capabilities.TotalMemoryMiB} MiB (managed view)");
        builder.AppendLine();
        builder.AppendLine("findings");
        foreach (var finding in capabilities.Findings)
        {
            builder.AppendLine($"  - {finding.Name}: {finding.ValueText}");
            builder.AppendLine($"      evidence   : {finding.Evidence}");
            builder.AppendLine($"      meaning    : {finding.Meaning}");
            builder.AppendLine($"      does not   : {finding.NotProven}");
        }

        builder.AppendLine();
        if (capabilities.Unknowns.Count > 0)
        {
            builder.AppendLine($"unestablished ({capabilities.Unknowns.Count}): {string.Join(", ", capabilities.Unknowns)}");
        }
        else
        {
            builder.AppendLine("unestablished: none");
        }

        builder.AppendLine();
        builder.AppendLine("This report describes a host. It says nothing about whether any");
        builder.AppendLine("PlayStation 5 operating-system component can execute on it; that");
        builder.AppendLine("question is answered by execution evidence, not by a capability list.");
        return builder.ToString();
    }

    public static string ToJson(HostCapabilities capabilities)
    {
        var payload = new
        {
            schema = "pc5.host-capabilities/1",
            generated = capabilities.ProbedAtUtc.ToString("O"),
            os = capabilities.OsDescription,
            architecture = capabilities.ProcessArchitecture,
            logicalProcessors = capabilities.LogicalProcessors,
            totalMemoryMiB = capabilities.TotalMemoryMiB,
            fullyDetermined = capabilities.FullyDetermined,
            findings = capabilities.Findings.Select(f => new
            {
                name = f.Name,
                supported = f.Supported,
                evidence = f.Evidence,
                meaning = f.Meaning,
                notProven = f.NotProven,
            }),
        };
        return System.Text.Json.JsonSerializer.Serialize(payload,
            new System.Text.Json.JsonSerializerOptions { WriteIndented = true });
    }
}

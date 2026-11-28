using System.Runtime.InteropServices;

namespace Pc5.Core.Diagnostics;

/// <summary>
/// The portable probe: everything PC5 can establish with the .NET runtime alone, on any
/// operating system. It reports the Windows-only and PS5-only questions as unknown rather
/// than guessing, which is why it can be unit-tested on a Linux runner and still be honest.
/// </summary>
public sealed class RuntimeHostProbe : IHostCapabilityProbe
{
    public string Id => "runtime-portable";

    public Task<HostCapabilities> DetectAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var isWindows = RuntimeInformation.IsOSPlatform(OSPlatform.Windows);
        var memoryMiB = GC.GetGCMemoryInfo().TotalAvailableMemoryBytes / (1024L * 1024L);

        var findings = new List<CapabilityFinding>
        {
            new()
            {
                Name = "host-is-windows",
                Supported = isWindows,
                Evidence = "RuntimeInformation.IsOSPlatform(OSPlatform.Windows)",
                Meaning = isWindows
                    ? "the host could run the PC5 Windows application"
                    : "the PC5 Windows application cannot run here",
                NotProven = "that Windows Hypervisor Platform, or any other backend, is usable",
            },
            new()
            {
                Name = "os-architecture-x64",
                Supported = RuntimeInformation.ProcessArchitecture == Architecture.X64,
                Evidence = $"RuntimeInformation.ProcessArchitecture == {RuntimeInformation.ProcessArchitecture}",
                Meaning = "the process is 64-bit, so a 64-bit guest design is not excluded by the host word size",
                NotProven = "that the CPU implements any specific instruction set a PS5 guest expects",
            },
            new()
            {
                Name = "cpu-features",
                Supported = null,
                Evidence = "not queried: PC5 exposes no CPUID surface in this build",
                Meaning = "unknown. A guest may depend on exact CPUID, topology or page-size behaviour",
                NotProven = "nothing either way -- this finding exists to record the absence of a measurement",
            },
            new()
            {
                Name = "virtualization-backend",
                Supported = null,
                Evidence = "not queried by the portable probe; see PC5.App/Platform for the Windows probe",
                Meaning = "unknown on this host",
                NotProven = "that a backend would be PS5-compatible even if it were present",
            },
            new()
            {
                Name = "memory-for-a-guest",
                Supported = memoryMiB >= 2048,
                Evidence = $"GC.GetGCMemoryInfo().TotalAvailableMemoryBytes = {memoryMiB} MiB",
                Meaning = "a 2 GiB floor is met by the runtime's own view of its budget",
                NotProven = "that contiguous guest memory could be reserved, or any figure a guest kernel requires",
            },
            new()
            {
                Name = "genuine-ps5-system-software-input",
                Supported = null,
                Evidence = "outside this probe's reach; see Pc5.Core.Firmware.FirmwareArtifact",
                Meaning = "unknown. Firmware availability is a separate, evidence-bearing question",
                NotProven = "nothing -- recorded so the report cannot imply the input exists",
            },
        };

        return Task.FromResult(new HostCapabilities
        {
            OsDescription = RuntimeInformation.OSDescription,
            ProcessArchitecture = RuntimeInformation.ProcessArchitecture.ToString(),
            LogicalProcessors = Environment.ProcessorCount,
            TotalMemoryMiB = memoryMiB,
            Findings = findings,
        });
    }
}
